#!/usr/bin/env python3
"""
ocr.py — the scanned-PDF path (image-only documents -> text).

Some towns post agendas/minutes as image-only PDFs that pdfplumber can't read
(extraction_status='skipped_scan'). This pass rasterizes each page with PyMuPDF
and has Claude (vision, via the same `claude -p` subscription backend) transcribe
them. The transcription is written as a sidecar `<pdf>.ocr.txt` next to the PDF
(in the gitignored raw/ archive) and the document flips back to 'pending', so
the standard extract.py run — same prompt, same validation — picks it up.

Idempotent: a doc with an existing sidecar is never re-transcribed.

Usage (from the repo root):
    python -m signals.extract.ocr [--limit N]
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

import fitz  # PyMuPDF

from signals import config, db
from signals.extract.extract import ExtractionError, call_claude

sys.stdout.reconfigure(encoding="utf-8")

RENDER_ZOOM = 2.8          # ~200 DPI — plenty for typed municipal documents
MIN_CHARS_PER_PAGE = 30    # below this, treat the transcription as failed

TRANSCRIBE_PROMPT = """\
Use the Read tool to view each of these page images IN ORDER, then transcribe
the complete text content of each page verbatim.

{image_list}

Rules:
- Transcribe exactly what is printed — do not summarize, correct, or omit.
- Preserve the reading order; keep headings, list numbering, dates, names,
  addresses, and dollar amounts exactly as written.
- If a word is illegible, write [illegible].
- Output format, with nothing before or after:
[PAGE 1]
<text of page 1>

[PAGE 2]
<text of page 2>
"""


def sidecar_path(pdf_path: str) -> str:
    return pdf_path + ".ocr.txt"


def render_pages(pdf_path: str, out_dir: str) -> list[str]:
    """Rasterize each PDF page to a PNG; return the image paths in order."""
    paths = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            pix = page.get_pixmap(matrix=fitz.Matrix(RENDER_ZOOM, RENDER_ZOOM))
            p = os.path.join(out_dir, f"page{i}.png")
            pix.save(p)
            paths.append(p)
    return paths


def transcribe(pdf_path: str) -> tuple[str, dict]:
    """Render + Claude-vision transcribe one PDF. Returns (text, usage).

    Images are rendered INSIDE the repo (signals/raw/.ocr_tmp, gitignored):
    headless `claude -p` auto-denies Read on paths outside the project.
    """
    tmp = os.path.join(config.RAW_DIR, ".ocr_tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        images = render_pages(pdf_path, tmp)
        if not images:
            raise ExtractionError("PDF has no pages")
        listing = "\n".join(f"Page {i}: {p}" for i, p in enumerate(images, 1))
        text, usage = call_claude(TRANSCRIBE_PROMPT.format(image_list=listing))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # Sanity: page markers present and enough text per page.
    pages_found = len(re.findall(r"\[PAGE \d+\]", text))
    body_chars = len(re.sub(r"\[PAGE \d+\]", "", text).strip())
    if pages_found < len(images) or body_chars < MIN_CHARS_PER_PAGE * len(images):
        raise ExtractionError(
            f"weak transcription ({pages_found}/{len(images)} page markers, "
            f"{body_chars} chars)")
    return text.strip(), usage


def main(argv=None):
    parser = argparse.ArgumentParser(description="OCR path for scanned documents.")
    parser.add_argument("--limit", type=int, default=None,
                        help="max documents to transcribe this run")
    args = parser.parse_args(argv)

    conn = db.init_db()
    docs = conn.execute(
        "SELECT * FROM documents WHERE extraction_status='skipped_scan' "
        "ORDER BY meeting_date ASC, doc_id ASC").fetchall()
    if args.limit:
        docs = docs[:args.limit]
    if not docs:
        print("No scanned documents waiting for OCR.")
        return

    print(f"OCR: {len(docs)} scanned document(s) via Claude vision "
          f"(`claude -p`, subscription auth).\n")
    done = failed = skipped = 0
    totals = {"in": 0, "out": 0}

    for doc in docs:
        path = os.path.join(config.REPO_ROOT, doc["local_path"].replace("/", os.sep))
        label = f"{doc['town_id']}/{doc['board_id']} {doc['doc_type']} {doc['meeting_date']}"
        if not os.path.exists(path):
            print(f"== {label} == MISSING PDF, skipped")
            skipped += 1
            continue
        side = sidecar_path(path)
        if os.path.exists(side):  # already transcribed — just requeue extraction
            conn.execute("UPDATE documents SET extraction_status='pending' "
                         "WHERE doc_id=?", (doc["doc_id"],))
            conn.commit()
            print(f"== {label} == sidecar exists, requeued for extraction")
            skipped += 1
            continue
        print(f"== {label} ==")
        try:
            text, usage = transcribe(path)
        except (ExtractionError, Exception) as exc:
            failed += 1
            print(f"   FAILED: {exc.__class__.__name__}: {exc}\n")
            continue
        with open(side, "w", encoding="utf-8") as fh:
            fh.write(text)
        conn.execute("UPDATE documents SET extraction_status='pending' "
                     "WHERE doc_id=?", (doc["doc_id"],))
        conn.commit()
        done += 1
        totals["in"] += usage.get("input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)
        totals["out"] += usage.get("output_tokens", 0)
        preview = re.sub(r"\s+", " ", re.sub(r"\[PAGE \d+\]", "", text))[:110]
        print(f"   transcribed {len(text):,} chars -> pending. \"{preview}...\"\n")

    print("=== OCR run summary ===")
    print(f"transcribed: {done} | failed: {failed} | skipped/requeued: {skipped}")
    print(f"tokens: {totals['in']:,} in / {totals['out']:,} out "
          f"($0 marginal — subscription)")
    if done or skipped:
        print("Next: python -m signals.extract.extract  (processes the requeued docs)")
    conn.close()


if __name__ == "__main__":
    main()
