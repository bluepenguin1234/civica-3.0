#!/usr/bin/env bash
# Rebuild the Civica site from current data, then validate.
#
#   ./rebuild.sh          FULL : re-score -> map geo -> pages -> validate   (needs ../civica_data/)
#   ./rebuild.sh --pages  FAST : pages -> validate                          (no civica_data needed)
#
# After a green run, commit + push to publish (see CLAUDE.md -> Deploy).
set -euo pipefail
cd "$(dirname "$0")"

if [ "${1:-}" = "--pages" ]; then
  echo ">> [1/2] regenerating pages…"
  python town_generator.py
else
  echo ">> [1/4] re-scoring (reads ../civica_data/)…"
  python town_scoring_engine.py
  echo ">> [2/4] rebuilding map geo…"
  python build_town_geo.py
  echo ">> [3/4] regenerating pages…"
  python town_generator.py
fi

echo ">> validating…"
python validate_town.py
echo ">> rebuild complete. If ALL GREEN, refresh any hardcoded stats on the static pages, then commit + push."
