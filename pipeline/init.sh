#!/usr/bin/env bash
# Idempotent town-build setup for a fresh context window (TOWN_HANDOFF.md §13b).
# Gets oriented without rediscovering commands: download -> score -> validate.
set -e
cd "$(dirname "$0")"

if [ ! -f ../civica_data/census_population/sub-est.csv ]; then
  echo "[init] sub-est missing -> downloading town datasets"
  python download_town_data.py
fi

if [ ! -f town_scores.csv ]; then
  echo "[init] town_scores.csv missing -> running town scoring engine"
  python town_scoring_engine.py
fi

echo "[init] validating"
python validate_town.py

# ── Civica Signals (separate subsystem; see ../signals/README.md) ──
# crawl -> extract -> link -> validate -> publish. Extraction runs through the
# Claude Code CLI on the operator's subscription (claude must be on PATH).
cd ..
echo "[init] signals: crawl"
python -m signals.crawl.crawl
echo "[init] signals: ocr (scanned docs)"
python -m signals.extract.ocr
echo "[init] signals: extract"
python -m signals.extract.extract
echo "[init] signals: link stories"
python -m signals.link.link_stories
echo "[init] signals: synthesize story briefs"
python -m signals.synthesize.build_briefs
echo "[init] signals: resolve entities (directory)"
python -m signals.enrich.resolve_entities
echo "[init] signals: enrich contacts (safe links)"
python -m signals.enrich.enrich_entities
echo "[init] signals: validate"
python -m signals.validate_signals
echo "[init] signals: publish feed"
python -m signals.publish.build_signals_json
echo "[init] signals: publish gated contacts"
python -m signals.publish.build_contacts_json
cd pipeline

echo "[init] done"
