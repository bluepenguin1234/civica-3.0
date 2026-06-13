"""Central configuration for the Civica Signals subsystem.

Path constants resolve via __file__ (matching the pipeline/ convention) so every
Signals script runs from any working directory. No secrets live here — the
Anthropic API key is read from the environment when extraction runs (Phase 2).
"""

import datetime as dt
import os

# --- Claude extraction (Phase 2) ---------------------------------------------
# Extraction runs through the Claude Code CLI headless (`claude -p --model ...`)
# on the operator's subscription — no API key/credits (see extract.call_claude,
# the only backend-specific function; swap to the anthropic SDK there if true
# unattended automation is ever needed). Sonnet balances precision and volume;
# bump to "claude-opus-4-8" if calibration shows precision gaps.
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# --- Canonical paths (resolved relative to this file) -----------------------
SIGNALS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SIGNALS_DIR)
REGISTRY_PATH = os.path.join(SIGNALS_DIR, "registry", "towns.yaml")
RAW_DIR = os.path.join(SIGNALS_DIR, "raw")           # downloaded PDFs (gitignored archive)
DB_PATH = os.path.join(SIGNALS_DIR, "signals.db")    # SQLite store (gitignored)
PUBLISH_DIR = os.path.join(REPO_ROOT, "docs", "output", "signals")  # JSON for the site

# --- Polite crawling (used in Phase 1) --------------------------------------
# Hard floor between requests to the SAME town website. These are small
# municipal servers — be a good citizen.
CRAWL_DELAY_SECONDS = 5.0

# Bids/RFP crawl (Step 3): fetch only the N most-recent bids per run. Bid IDs are
# monotonic (newest = highest), and the index has no per-row dates, so we bound
# the fetch to the newest N and date-filter each detail page after fetching.
BIDS_RECENT_COUNT = 8

# Rolling crawl window: only crawl documents newer than this many days (~3 months).
# Signals tracks current development activity; the historical archive accrues
# naturally from repeated weekly runs, so we don't deep-backfill.
CRAWL_LOOKBACK_DAYS = 90
# Per-request network policy (applies to listing fetches and PDF downloads).
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

# Identify ourselves honestly so a town clerk can reach a human.
# TODO: replace with a real, monitored inbox before crawling live sites.
CONTACT_EMAIL = "TODO-set-contact@example.com"
USER_AGENT = (
    "CivicaSignals/0.1 "
    "(+https://bluepenguin1234.github.io/civica-3.0/; "
    "municipal public-records monitor; "
    f"contact: {CONTACT_EMAIL})"
)


def crawl_cutoff_date():
    """ISO date floor for crawling: today minus the rolling lookback window."""
    return (dt.date.today() - dt.timedelta(days=CRAWL_LOOKBACK_DAYS)).isoformat()
