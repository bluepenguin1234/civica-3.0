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
echo "[init] done"
