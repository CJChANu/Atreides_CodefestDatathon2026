#!/bin/bash
# Builds ../Atreides_CodefestDatathon2026.zip for submission.
# Excludes the rebuildable warehouse (python src/warehouse.py), virtualenvs, caches and node_modules.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="Atreides_CodefestDatathon2026.zip"
rm -f "$OUT"
zip -rq "$OUT" Atreides_CodefestDatathon2026 \
  -x "*/data/warehouse.duckdb" "*/.venv/*" "*/__pycache__/*" "*/node_modules/*" "*/.ipynb_checkpoints/*" "*/.DS_Store" "*/.claude/*" "*/~\$*"
ls -lh "$OUT"
