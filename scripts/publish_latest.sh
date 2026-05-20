#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

COMMIT_MESSAGE="${1:-Update Kobo 99 latest list}"

python3 scripts/kobo99.py --out public

git add public/events.json public/kobo99.ics public/index.html

if git diff --cached --quiet; then
  echo "No Kobo 99 output changes to commit."
else
  git commit -m "$COMMIT_MESSAGE"
fi

git push
