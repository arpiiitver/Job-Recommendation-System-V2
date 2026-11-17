#!/usr/bin/env bash
# Assemble the production build context in a clean directory:
# backend source (no caches, uploads, local env files) + this folder's
# Dockerfile / start.sh / requirements.prod.txt.
#
#   bash deploy/backend/stage.sh <target-dir>
set -euo pipefail

TARGET="${1:?usage: stage.sh <target-dir>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

mkdir -p "$TARGET"
# replace everything (keep a .git if one exists)
find "$TARGET" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +

tar -C "$ROOT/backend" \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.pytest_cache' \
  --exclude='uploads/*' --exclude='.env*' --exclude='ml/data/raw' \
  -cf - . | tar -C "$TARGET" -xf -

cp "$ROOT/deploy/backend/Dockerfile" "$ROOT/deploy/backend/start.sh" \
   "$ROOT/deploy/backend/requirements.prod.txt" "$TARGET/"
# shell scripts must be LF inside the Linux image (Windows checkouts add CR)
sed -i 's/\r$//' "$TARGET/start.sh"
mkdir -p "$TARGET/uploads"
touch "$TARGET/uploads/.gitkeep"

# guard: never ship secrets
if grep -rIl --exclude-dir=.git -E '(AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,})' "$TARGET" >/dev/null; then
  echo "ERROR: something that looks like an API key is in the staged files:" >&2
  grep -rIl --exclude-dir=.git -E '(AIza[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9]{20,})' "$TARGET" >&2
  exit 1
fi
echo "staged $(find "$TARGET" -type f ! -path '*/.git/*' | wc -l) files into $TARGET"
