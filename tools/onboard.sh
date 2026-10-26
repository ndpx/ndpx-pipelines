#!/usr/bin/env bash
# Bring existing local code into the ndpx GitHub organisation, through the ndpx checks.
#
# What it does:
#   1. Clones the EMPTY ndpx repository (create it first in GitHub, Private, no README).
#   2. Adds the ndpx pipeline files on `main` and pushes them.
#   3. Copies your local folder (current files only, NOT its old Git history) onto branch `import/initial`,
#      leaving out secrets files, virtual environments and build output.
#   4. Warns about anything that looks like a secret before pushing, and asks you to confirm.
#   5. Pushes the branch and prints the link to open the pull request. Every file is then checked.
#
# Usage (Git Bash):
#   bash /c/dev/ndpx-pipelines/tools/onboard.sh <path-to-local-folder> <repo-name> [risk-tier]
# Example:
#   bash /c/dev/ndpx-pipelines/tools/onboard.sh "/c/Users/ivan/glue-jobs" aws-glue-jobs 2
set -euo pipefail

SRC="${1:-}"
REPO="${2:-}"
TIER="${3:-2}"
ORG_URL="${NDPX_REMOTE_BASE:-https://github.com/ndpx}"
DEV_DIR="${NDPX_DEV_DIR:-/c/dev}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STARTER="$HERE/starter"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -n "$SRC" ] && [ -n "$REPO" ] || die "usage: onboard.sh <local-folder> <repo-name> [risk-tier 1|2|3]"
[ -d "$SRC" ] || die "local folder not found: $SRC"
[[ "$REPO" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "repo name must be lower case letters, digits and dashes"
[[ "$TIER" =~ ^[123]$ ]] || die "risk tier must be 1, 2 or 3"
[ -d "$STARTER" ] || die "starter files not found at $STARTER (run from a clone of ndpx-pipelines)"
case "$(cd "$SRC" && pwd)" in *OneDrive*|*SharePoint*|*" - Documents"*)
  echo "NOTE: the source is in a synced folder. That is fine to copy FROM; the new repository goes to $DEV_DIR." ;;
esac

REMOTE="$ORG_URL/$REPO.git"
DEST="$DEV_DIR/$REPO"
[ ! -e "$DEST" ] || die "$DEST already exists. Move it away or choose another name."

echo "== 1. Checking $REMOTE exists and is empty"
refs="$(git ls-remote "$REMOTE" 2>/dev/null)" || die "cannot reach $REMOTE. Create it in GitHub first (Private, no README)."
[ -z "$refs" ] || die "$REMOTE is not empty. Onboarding needs an empty repository."

echo "== 2. Adding ndpx pipeline files on main"
mkdir -p "$DEV_DIR"
git init -q -b main "$DEST"
cd "$DEST"
git config core.autocrlf false
git remote add origin "$REMOTE"
cp -R "$STARTER/." .
sed -i "s/^risk_tier: [0-9]/risk_tier: $TIER/" .ndpx/pipeline.yml
git add -A
git commit -q -m "Add ndpx pipeline configuration"
git push -q -u origin main

echo "== 3. Copying your code (current files only, no old history) onto branch import/initial"
git checkout -q -b import/initial
EXCLUDES=(--exclude=.git --exclude=node_modules --exclude=.venv --exclude=venv --exclude=__pycache__
  --exclude=dist --exclude=build --exclude=coverage --exclude=.pytest_cache --exclude=.ruff_cache
  --exclude='.env' --exclude='.env.*' --exclude='*.pem' --exclude='*.key' --exclude='*.pfx' --exclude='*.p12'
  --exclude='*.jks' --exclude='*.keystore' --exclude='id_rsa*' --exclude='id_ed25519*' --exclude='*.tfstate*'
  --exclude='.DS_Store' --exclude='Thumbs.db' --exclude='*.log' --exclude='.idea')
# Report what is being left out because it may hold secrets.
left_out="$(cd "$SRC" && find . \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' -o -name '*.pfx' \
  -o -name '*.p12' -o -name '*.jks' -o -name '*.keystore' -o -name 'id_rsa*' -o -name 'id_ed25519*' -o -name '*.tfstate*' \) \
  -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null || true)"
tar -C "$SRC" -cf - "${EXCLUDES[@]}" . | tar -C "$DEST" -xf -
# Keep the ndpx versions of pipeline files if the source had its own.
git checkout -q main -- .github .ndpx .gitattributes AGENTS.md CLAUDE.md 2>/dev/null || true
for f in AGENTS.md CLAUDE.md; do
  if [ -f "$SRC/$f" ]; then { echo; echo "## Project-specific notes (from the original $f)"; echo; cat "$SRC/$f"; } >> "$f"; fi
done
if [ -f "$SRC/.gitignore" ]; then
  { echo; echo "# --- from original project ---"; cat "$SRC/.gitignore"; } >> .gitignore.tmp
  git show main:.gitignore > .gitignore && cat .gitignore.tmp >> .gitignore && rm -f .gitignore.tmp
fi

echo "== 4. Looking for anything that resembles a secret"
suspects="$(grep -rInE --exclude-dir=.git \
  -e 'AKIA[0-9A-Z]{16}' \
  -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  -e 'sb_secret_[A-Za-z0-9_-]{10,}' \
  -e '(password|passwd|pwd|secret|api_key|apikey|token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'$ {}]{6,}' \
  -e 'IDENTIFIED[[:space:]]+BY[[:space:]]+["'"'"']?[A-Za-z0-9!@#%^&*]{4,}' \
  . 2>/dev/null | grep -v '^./.gitignore' | head -40 || true)"
if [ -n "$left_out" ]; then
  echo
  echo "Left out (may hold secrets; keep them out of Git, store values in a secrets manager):"
  echo "$left_out" | sed 's/^/   /'
fi
if [ -n "$suspects" ]; then
  echo
  echo "Possible secrets found in files that WILL be pushed:"
  echo "$suspects" | sed 's/^/   /'
  echo
  echo "Remove them from the files first. If any are real credentials, rotate them anyway."
  read -r -p "Push anyway? Type YES to continue, anything else to stop: " answer
  [ "$answer" = "YES" ] || { echo "Stopped. Nothing pushed except the pipeline files on main. Fix the files in $DEST, then:"; \
    echo "  cd $DEST && git add -A && git commit -m 'Import existing code' && git push -u origin import/initial"; exit 1; }
fi

git add -A
if git diff --cached --quiet; then die "no files to import from $SRC"; fi
count="$(git diff --cached --name-only | wc -l | tr -d ' ')"
git commit -q -m "Import existing code from local folder"
git push -q -u origin import/initial

echo
echo "== Done. $count file(s) pushed to branch import/initial."
echo "Open the pull request so every file is checked:"
echo "   ${ORG_URL/https:\/\/github.com/https://github.com}/$REPO/compare/main...import/initial?expand=1"
echo
echo "Then in GitHub: Settings > Secrets and variables > Actions > Variables > add CODE_REVIEWERS."
echo "Work from $DEST from now on."
