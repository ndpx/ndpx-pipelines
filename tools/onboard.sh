#!/usr/bin/env bash
# Bring code into the ndpx GitHub organisation, through the ndpx checks.
#
# Two modes (Git Bash):
#
#   import    Code that is NOT yet in the ndpx organisation (laptop, OneDrive, personal hosting).
#             Needs an EMPTY repository created in GitHub first (Private, no README).
#               bash /c/dev/ndpx-pipelines/tools/onboard.sh import "<local-folder>" <repo-name> [risk-tier]
#             - adds the ndpx files on main
#             - copies current files only (not old Git history) onto branch import/initial,
#               leaving out .env files, keys, certificates, virtual environments and build output
#             - warns about anything that looks like a secret and asks you to confirm
#             - pushes and prints the pull request link
#
#   existing  A repository that is ALREADY in the ndpx organisation (including one transferred in).
#               bash /c/dev/ndpx-pipelines/tools/onboard.sh existing <repo-name> [risk-tier]
#             - clones it to /c/dev/<repo-name> (or uses your clone there)
#             - adds the ndpx files on branch ndpx/add-pipeline without overwriting your files
#             - merges the .gitignore, sets up Dependabot, lists anything that looks like a secret
#             - pushes and prints the pull request link. The first run also scans the full Git history.
#
# The older form "onboard.sh <local-folder> <repo-name> [risk-tier]" still works and means import.
set -euo pipefail

ORG_URL="${NDPX_REMOTE_BASE:-https://github.com/ndpx}"
DEV_DIR="${NDPX_DEV_DIR:-/c/dev}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STARTER="$HERE/starter"
WAIVER='design:\n  gate: warn # TEMPORARY waiver for existing code. Complete docs/DESIGN.md, get it approved, then delete this.\n'

die() { echo "ERROR: $*" >&2; exit 1; }
usage() {
  sed -n '4,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 1
}

# ------------------------------------------------------------------ shared steps
check_name_tier() {
  [[ "$REPO" =~ ^[a-z0-9][a-z0-9-]*$ ]] || die "repo name must be lower case letters, digits and dashes"
  [[ "$TIER" =~ ^[123]$ ]] || die "risk tier must be 1, 2 or 3"
  [ -d "$STARTER" ] || die "starter files not found at $STARTER (run from a clone of ndpx-pipelines)"
}

set_tier_and_waiver() {
  sed -i "s/^risk_tier: [0-9]/risk_tier: $TIER/" .ndpx/pipeline.yml
  # Existing code has no approved design note yet. Record a visible, temporary waiver (Software Delivery Standard 17.1).
  if [ "$TIER" != "1" ]; then printf "\n$WAIVER" >> .ndpx/pipeline.yml; fi
}

add_dependabot_for() {   # $1 = folder to inspect
  local dir="$1"
  has() { find "$dir" -path '*/node_modules' -prune -o -path '*/.git' -prune -o -type f \( "$@" \) -print -quit 2>/dev/null | grep -q .; }
  add_eco() { printf '  - package-ecosystem: %s\n    directories: ["/", "/**/*"]\n    schedule: { interval: weekly }\n    cooldown: { default-days: 7 }\n' "$1" >> .github/dependabot.yml; }
  if has -name 'requirements*.txt' -o -name 'pyproject.toml'; then add_eco pip; fi
  if has -name 'Dockerfile*' -o -name '*.dockerfile'; then add_eco docker; fi
  if has -name 'package.json'; then add_eco npm; fi
}

find_secrets() {         # prints suspicious lines in the current folder
  grep -rInE --exclude-dir=.git --exclude-dir=node_modules \
    -e 'AKIA[0-9A-Z]{16}' \
    -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' \
    -e 'sb_secret_[A-Za-z0-9_-]{10,}' \
    -e '(password|passwd|pwd|secret|api_key|apikey|token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'$ {}]{6,}' \
    -e 'IDENTIFIED[[:space:]]+BY[[:space:]]+["'"'"']?[A-Za-z0-9!@#%^&*]{4,}' \
    . 2>/dev/null | grep -v '^./.gitignore' | head -40 || true
}

merge_gitignore() {      # append starter lines that the repository's .gitignore lacks
  local added=0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    if ! grep -qxF -- "$line" .gitignore; then
      [ $added -eq 0 ] && printf '\n# --- ndpx defaults (secrets, environments, build output) ---\n' >> .gitignore
      echo "$line" >> .gitignore; added=1
    fi
  done < <(grep -v '^#' "$STARTER/.gitignore")
}

pr_link() { echo "${ORG_URL}/$REPO/compare/$1...$2?expand=1"; }

after_steps() {
  echo
  echo "Then:"
  echo "  1. Open the pull request (link above). Every check runs. Send the results to a Code Reviewer."
  echo "  2. GitHub > $REPO > Settings > Secrets and variables > Actions > Variables: add CODE_REVIEWERS."
  echo "  3. Give the builders' team Write access: Settings > Collaborators and teams."
  echo "Work from $DEST from now on."
}

# ------------------------------------------------------------------ import
cmd_import() {
  SRC="${1:-}"; REPO="${2:-}"; TIER="${3:-2}"
  [ -n "$SRC" ] && [ -n "$REPO" ] || usage
  [ -d "$SRC" ] || die "local folder not found: $SRC"
  check_name_tier
  case "$(cd "$SRC" && pwd)" in *OneDrive*|*SharePoint*|*" - Documents"*)
    echo "NOTE: the source is in a synced folder. That is fine to copy FROM; the new repository goes to $DEV_DIR." ;;
  esac
  REMOTE="$ORG_URL/$REPO.git"; DEST="$DEV_DIR/$REPO"
  [ ! -e "$DEST" ] || die "$DEST already exists. Move it away or choose another name."

  echo "== 1. Checking $REMOTE exists and is empty"
  refs="$(git ls-remote "$REMOTE" 2>/dev/null)" || die "cannot reach $REMOTE. Create it in GitHub first (Private, no README)."
  [ -z "$refs" ] || die "$REMOTE is not empty. For a repository that already has code, use: onboard.sh existing $REPO $TIER"

  echo "== 2. Adding ndpx pipeline files on main"
  mkdir -p "$DEV_DIR"
  git init -q -b main "$DEST"; cd "$DEST"
  git config core.autocrlf false
  git remote add origin "$REMOTE"
  cp -R "$STARTER/." .
  set_tier_and_waiver
  add_dependabot_for "$SRC"
  git add -A; git commit -q -m "Add ndpx pipeline configuration"; git push -q -u origin main

  echo "== 3. Copying your code (current files only, no old history) onto branch import/initial"
  git checkout -q -b import/initial
  local excludes=(--exclude=.git --exclude=node_modules --exclude=.venv --exclude=venv --exclude=__pycache__
    --exclude=dist --exclude=build --exclude=coverage --exclude=.pytest_cache --exclude=.ruff_cache
    --exclude='.env' --exclude='.env.*' --exclude='*.pem' --exclude='*.key' --exclude='*.pfx' --exclude='*.p12'
    --exclude='*.jks' --exclude='*.keystore' --exclude='id_rsa*' --exclude='id_ed25519*' --exclude='*.tfstate*'
    --exclude='.DS_Store' --exclude='Thumbs.db' --exclude='*.log' --exclude='.idea')
  local left_out
  left_out="$(cd "$SRC" && find . \( -name '.env' -o -name '.env.*' -o -name '*.pem' -o -name '*.key' -o -name '*.pfx' \
    -o -name '*.p12' -o -name '*.jks' -o -name '*.keystore' -o -name 'id_rsa*' -o -name 'id_ed25519*' -o -name '*.tfstate*' \) \
    -not -path '*/node_modules/*' -not -path '*/.git/*' 2>/dev/null || true)"
  tar -C "$SRC" -cf - "${excludes[@]}" . | tar -C "$DEST" -xf -
  # Keep the ndpx versions of pipeline files if the source had its own.
  git checkout -q main -- .github .ndpx .gitattributes AGENTS.md CLAUDE.md 2>/dev/null || true
  for f in AGENTS.md CLAUDE.md; do
    if [ -f "$SRC/$f" ]; then { echo; echo "## Project-specific notes (from the original $f)"; echo; cat "$SRC/$f"; } >> "$f"; fi
  done
  if [ -f "$SRC/.gitignore" ]; then
    { echo; echo "# --- from original project ---"; cat "$SRC/.gitignore"; } > .gitignore.tmp
    git show main:.gitignore > .gitignore && cat .gitignore.tmp >> .gitignore && rm -f .gitignore.tmp
  fi

  echo "== 4. Looking for anything that resembles a secret"
  local suspects; suspects="$(find_secrets)"
  if [ -n "$left_out" ]; then
    echo; echo "Left out (may hold secrets; keep them out of Git, store values in a secrets manager):"
    echo "$left_out" | sed 's/^/   /'
  fi
  if [ -n "$suspects" ]; then
    echo; echo "Possible secrets found in files that WILL be pushed:"; echo "$suspects" | sed 's/^/   /'
    echo; echo "Remove them from the files first. If any are real credentials, change them at the source anyway."
    read -r -p "Push anyway? Type YES to continue, anything else to stop: " answer
    [ "$answer" = "YES" ] || { echo "Stopped. Nothing pushed except the pipeline files on main. Fix the files in $DEST, then:"
      echo "  cd $DEST && git add -A && git commit -m 'Import existing code' && git push -u origin import/initial"; exit 1; }
  fi

  git add -A
  if git diff --cached --quiet; then die "no files to import from $SRC"; fi
  local count; count="$(git diff --cached --name-only | wc -l | tr -d ' ')"
  git commit -q -m "Import existing code from local folder"
  git push -q -u origin import/initial
  echo; echo "== Done. $count file(s) pushed to branch import/initial."
  echo "Open the pull request so every file is checked:"; echo "   $(pr_link main import/initial)"
  after_steps
}

# ------------------------------------------------------------------ existing
cmd_existing() {
  REPO="${1:-}"; TIER="${2:-2}"
  [ -n "$REPO" ] || usage
  check_name_tier
  REMOTE="$ORG_URL/$REPO.git"; DEST="$DEV_DIR/$REPO"; BRANCH="ndpx/add-pipeline"

  echo "== 1. Checking $REMOTE"
  local head; head="$(git ls-remote --symref "$REMOTE" HEAD 2>/dev/null)" || die "cannot reach $REMOTE. Check the name and your access."
  local default; default="$(echo "$head" | sed -n 's#^ref: refs/heads/\([^[:space:]]*\)[[:space:]]*HEAD#\1#p')"
  [ -n "$default" ] || die "$REMOTE is empty. For a new repository use: onboard.sh import <local-folder> $REPO $TIER"

  echo "== 2. Getting the code into $DEST"
  if [ -d "$DEST/.git" ]; then
    cd "$DEST"
    [ -z "$(git status --porcelain)" ] || die "$DEST has uncommitted changes. Commit or stash them first."
    git fetch -q origin
  else
    [ ! -e "$DEST" ] || die "$DEST exists but is not a Git repository. Move it away first."
    mkdir -p "$DEV_DIR"; git clone -q "$REMOTE" "$DEST"; cd "$DEST"
  fi
  git config core.autocrlf false
  git cat-file -e "origin/$default:.ndpx/pipeline.yml" 2>/dev/null && die "$REPO already has .ndpx/pipeline.yml. It is already onboarded."
  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    die "branch $BRANCH already exists on GitHub. Finish or close that pull request, or delete the branch, then run again."
  fi
  git checkout -q -B "$BRANCH" "origin/$default"

  echo "== 3. Adding ndpx files on branch $BRANCH (your existing files are not overwritten)"
  local kept=()
  for f in .github/CODEOWNERS .github/pull_request_template.md .github/dependabot.yml .gitattributes; do
    [ -e "$f" ] && kept+=("$f")
  done
  local had_gitignore=0 had_dependabot=0
  [ -f .gitignore ] && had_gitignore=1
  [ -f .github/dependabot.yml ] && had_dependabot=1
  for f in AGENTS.md CLAUDE.md; do [ -f "$f" ] && mv "$f" "$f.ndpx-orig"; done
  cp -Rn "$STARTER/." . 2> >(grep -v 'non-portable' >&2)
  for f in AGENTS.md CLAUDE.md; do
    if [ -f "$f.ndpx-orig" ]; then { echo; echo "## Project-specific notes (from the original $f)"; echo; cat "$f.ndpx-orig"; } >> "$f"; rm -f "$f.ndpx-orig"; fi
  done
  [ $had_gitignore -eq 1 ] && merge_gitignore
  set_tier_and_waiver
  [ $had_dependabot -eq 0 ] && add_dependabot_for "."

  echo "== 4. Looking for anything that resembles a secret in the current files"
  local suspects; suspects="$(find_secrets)"
  if [ -n "$suspects" ]; then
    echo; echo "Possible secrets ALREADY in this repository:"; echo "$suspects" | sed 's/^/   /'
    echo "They are already on GitHub. Change any real credential at its source, then remove it from the file in this pull request."
  fi
  local commits; commits="$(git rev-list --count HEAD)"

  git add -A
  git commit -q -m "Add ndpx pipeline configuration"
  git push -q -u origin "$BRANCH"
  echo; echo "== Done. ndpx files pushed to branch $BRANCH."
  if [ ${#kept[@]} -gt 0 ]; then
    echo "Kept your existing: ${kept[*]}"
    echo "  Check .github/CODEOWNERS includes @ndpx/code-reviewers, and dependabot.yml has 'cooldown: { default-days: 7 }'."
  fi
  echo "The first pull request run also scans all $commits past commit(s) for secrets and reports what it finds."
  echo "Open the pull request:"; echo "   $(pr_link "$default" "$BRANCH")"
  after_steps
}

case "${1:-}" in
  import) shift; cmd_import "$@" ;;
  existing) shift; cmd_existing "$@" ;;
  ""|-h|--help|help) usage ;;
  *) cmd_import "$@" ;;   # older form: onboard.sh <folder> <repo> [tier]
esac
