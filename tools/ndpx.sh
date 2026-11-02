#!/usr/bin/env bash
# ndpx helper: the everyday Git steps, done safely. Run inside a repository in C:\dev.
#
#   ndpx start "short description" [feat|fix]   new branch from the latest main
#   ndpx check                                  run the same checks as the pipeline, locally
#   ndpx ship "what changed"                    check, commit, push, print the pull request link
#   ndpx done                                   after merge: back to main, update, delete the branch
#   ndpx status                                 where am I, what is uncommitted
#   ndpx rules                                  add or refresh the Git workflow rules in AGENTS.md / CLAUDE.md
#   ndpx pin                                    pin the production deploy to the current reviewed v1 commit
#   ndpx sync [--apply]                         compare shared files with the template; --apply copies the latest
#   ndpx install                                make the `ndpx` command available in Git Bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
die() { echo "ndpx: $*" >&2; exit 1; }
say() { echo "== $*"; }
usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1; }

in_repo() { git rev-parse --show-toplevel >/dev/null 2>&1 || die "not inside a Git repository. cd into C:\\dev\\<repo> first."; cd "$(git rev-parse --show-toplevel)"; }
default_branch() {
  local b
  b="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')" || true
  if [ -z "$b" ]; then git remote set-head origin --auto >/dev/null 2>&1 || true
    b="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')" || true; fi
  echo "${b:-main}"
}
current() { git rev-parse --abbrev-ref HEAD; }
dirty() { [ -n "$(git status --porcelain)" ]; }
slug() { echo "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+|-+$//g' | cut -c1-50; }
repo_url() { git remote get-url origin | sed -E 's#^git@github.com:#https://github.com/#; s#\.git$##'; }

cmd_start() {
  in_repo
  local desc="${1:-}" type="${2:-}"
  [ -n "$desc" ] || die 'usage: ndpx start "short description" [feat|fix]'
  if [ -z "$type" ]; then case "$(echo "$desc" | tr '[:upper:]' '[:lower:]')" in fix*|bug*|repair*|correct*|update*|upgrade*) type=fix;; *) type=feat;; esac; fi
  [[ "$type" =~ ^(feat|fix|hotfix|chore)$ ]] || die "type must be feat, fix, hotfix or chore"
  local main; main="$(default_branch)" carry=0
  if dirty; then
    carry=1; say "Moving your uncommitted changes from '$(current)' to the new branch"
    git stash push -q -u -m "ndpx start"
  fi
  local br; br="$type/$(slug "$desc")"
  git show-ref --verify --quiet "refs/heads/$br" && { [ $carry -eq 1 ] && git stash pop -q; die "branch $br already exists locally. Choose another description."; }
  say "Updating $main"
  git fetch --prune -q origin
  git checkout -q "$main" && git pull -q --ff-only origin "$main"
  git checkout -q -b "$br"
  if [ $carry -eq 1 ]; then
    git stash pop -q || die "your changes conflict with the latest $main. They are kept in 'git stash list'; ask for help."
  fi
  say "On new branch $br, based on the latest $main. Make your change, then: ndpx ship \"what changed\""
}

check_node() {
  [ -f package.json ] || return 0
  command -v node >/dev/null || die "Node.js is not installed. Install Node LTS from Company Portal."
  local want="" have; have="$(node -v | sed 's/^v//')"
  [ -f .nvmrc ] && want="$(tr -d ' \r\nv' < .nvmrc)"
  [ -z "$want" ] && [ -f .node-version ] && want="$(tr -d ' \r\nv' < .node-version)"
  if [ -n "$want" ] && [ "${have%%.*}" != "${want%%.*}" ]; then
    die "this project uses Node $want (see .nvmrc) but you have Node $have. Install the right version first."
  fi
  if [ -f netlify.toml ] && [ -n "$want" ]; then
    local nv; nv="$(grep -E '^[[:space:]]*NODE_VERSION[[:space:]]*=' netlify.toml | head -1 | sed -E 's/.*=[[:space:]]*"?([^"]*)"?.*/\1/' || true)"
    if [ -n "$nv" ] && [ "${nv%%.*}" != "${want%%.*}" ]; then
      die "netlify.toml builds with NODE_VERSION=$nv but .nvmrc says $want. Netlify would build differently. Make them match."
    fi
  fi
}

cmd_check() {
  in_repo
  local ran=0
  if [ -f package.json ]; then
    ran=1; check_node
    has() { node -e "process.exit(require('./package.json').scripts?.['$1'] ? 0 : 1)"; }
    say "npm ci"; npm ci --no-audit --no-fund
    for s in format:check lint typecheck test:coverage test build; do
      if has "$s"; then
        [ "$s" = "test" ] && has test:coverage && continue
        say "npm run $s"; npm run "$s"
      fi
    done
    say "npm audit (high and critical)"; npm audit --audit-level=high
  fi
  if ls requirements*.txt >/dev/null 2>&1 || [ -f pyproject.toml ]; then
    ran=1
    if command -v python >/dev/null || command -v python3 >/dev/null; then
      local py; py="$(command -v python3 || command -v python)"
      if ls tests/*.py >/dev/null 2>&1 || ls test_*.py >/dev/null 2>&1; then say "pytest"; "$py" -m pytest -q; fi
      say "Python syntax check"; "$py" -m compileall -q . >/dev/null
    else
      echo "   Python not installed here: Python checks will run in the pipeline only."
    fi
  fi
  say "Looking for secrets in changed files"
  local found
  found="$(git diff --name-only HEAD 2>/dev/null; git ls-files --others --exclude-standard)"
  if [ -n "$found" ] && echo "$found" | xargs -r grep -InE \
      -e 'AKIA[0-9A-Z]{16}' -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' -e 'sb_secret_[A-Za-z0-9_-]{10,}' \
      -e '(password|passwd|pwd|secret|api_key|apikey|token)[[:space:]]*[:=][[:space:]]*["'"'"'][^"'"'"'$ {}]{6,}' \
      2>/dev/null; then
    die "possible secret above. Remove it before shipping."
  fi
  [ $ran -eq 1 ] || echo "   No npm or Python project found: the pipeline still runs secret, code and SQL/Docker checks."
  say "Local checks passed. The pipeline runs the full set on the pull request."
}

cmd_ship() {
  in_repo
  local msg="${1:-}" br main
  [ -n "$msg" ] || die 'usage: ndpx ship "what changed"'
  br="$(current)"; main="$(default_branch)"
  [ "$br" != "$main" ] || die "you are on $main. Run: ndpx start \"description\" first (your changes come with you)."
  [ "$br" != "HEAD" ] || die "not on a branch."
  git fetch --prune -q origin
  if git config --get "branch.$br.merge" >/dev/null 2>&1 \
     && ! git ls-remote --exit-code --heads origin "$br" >/dev/null 2>&1; then
    die "branch $br was merged and deleted on GitHub. Do not reuse it. Run: ndpx start \"description\" (your uncommitted changes move to the new branch), then ndpx ship again."
  fi
  if command -v gh >/dev/null && gh pr list --head "$br" --state merged --json number --jq '.[0].number' 2>/dev/null | grep -q .; then
    die "the pull request for $br is already merged. Do not reuse it. Run: ndpx start \"description\" (your uncommitted changes move to the new branch), then ndpx ship again."
  fi
  cmd_check
  if dirty; then git add -A && git commit -q -m "$msg"; fi
  git push -q -u origin "$br"
  say "Pushed $br."
  echo "Open or view the pull request: $(repo_url)/compare/$main...$br?expand=1"
  echo "If that page offers 'Create pull request', create it. If it shows the pull request as merged, this branch is"
  echo "finished: run ndpx done, then ndpx start \"description\" and ship again."
  echo "Then: wait for the 'result' check, ask a Code Reviewer to review. After it is merged: ndpx done"
}

cmd_done() {
  in_repo
  local br main; br="$(current)"; main="$(default_branch)"
  dirty && die "uncommitted changes on $br. Ship them, or move them to a new branch with: ndpx start \"description\"."
  git fetch --prune -q origin
  git checkout -q "$main" && git pull -q --ff-only origin "$main"
  if [ "$br" != "$main" ]; then
    if git ls-remote --exit-code --heads origin "$br" >/dev/null 2>&1; then
      echo "   Note: $br still exists on GitHub. If its pull request is merged, delete it there (or turn on auto-delete)."
    fi
    git branch -D "$br" >/dev/null && say "Deleted local branch $br."
  fi
  say "On $main, up to date. Start the next change with: ndpx start \"description\""
}

cmd_status() {
  in_repo
  echo "Repository: $(repo_url)"
  echo "Branch:     $(current) (main branch: $(default_branch))"
  git status -sb | head -20
}

cmd_rules() {
  in_repo
  local blk="$HERE/templates/git-workflow.md"
  [ -f "$blk" ] || die "rules template not found at $blk. Update ndpx-pipelines (git pull)."
  [ -f AGENTS.md ] || printf '# Rules for AI coding assistants in this repository\n' > AGENTS.md
  if grep -q 'ndpx:git-workflow:start' AGENTS.md; then
    awk -v f="$blk" '/ndpx:git-workflow:start/{while((getline l < f)>0) print l; skip=1; next} /ndpx:git-workflow:end/{skip=0; next} !skip' AGENTS.md > AGENTS.md.tmp && mv AGENTS.md.tmp AGENTS.md
  else
    { echo; cat "$blk"; } >> AGENTS.md
  fi
  if [ ! -f CLAUDE.md ] || ! grep -q '^@AGENTS.md' CLAUDE.md; then
    { printf '# CLAUDE.md\n\nClaude Code: the rules for this repository are in AGENTS.md. Follow all of them, including the Git workflow.\n\n@AGENTS.md\n'
      if [ -f CLAUDE.md ] && grep -q '^## Project-specific notes' CLAUDE.md; then echo; sed -n '/^## Project-specific notes/,$p' CLAUDE.md; fi
    } > CLAUDE.md.tmp && mv CLAUDE.md.tmp CLAUDE.md
  fi
  say "AGENTS.md and CLAUDE.md now contain the current Git workflow rules. Ship this as a normal change."
}

cmd_pin() {
  in_repo
  local f=.github/workflows/deploy.yml sha
  [ -f "$f" ] && grep -q 'ndpx-production-pin' "$f" || die "no production pin markers in $f (web apps from the template only)."
  git -C "$HERE" fetch -q --tags --force origin || die "cannot update $HERE; check your network and access."
  sha="$(git -C "$HERE" rev-parse 'v1^{commit}')" || die "tag v1 not found in $HERE."
  sed -i -E "/ndpx-production-pin/ s#(deploy\.yml@)[^ ]+#\1${sha}#; /ndpx-production-pin/ s#(pipelines-ref: )[^ ]+#\1${sha}#" "$f"
  git --no-pager diff --stat -- "$f"
  say "Production now uses pipeline commit ${sha:0:12} (current v1). Ship this as a normal change: ndpx ship \"Pin production pipeline\""
}

# Shared files a repository should keep identical to its source (the web app template or starter/).
# Repository-specific files (CODEOWNERS, .ndpx/pipeline.yml, app code) are never touched.
WEB_FILES=".github/workflows/ci.yml .github/workflows/deploy.yml .github/dependabot.yml .github/pull_request_template.md .gitattributes .prettierrc.json src/lib/keyGuard.ts src/lib/keyGuard.test.ts"
STARTER_FILES=".github/workflows/checks.yml .github/dependabot.yml .github/pull_request_template.md .gitattributes"

cmd_sync() {
  in_repo
  local apply=0 src kind files f want have state changed=0 pinned=0
  [ "${1:-}" = "--apply" ] && apply=1
  if [ -f .github/workflows/ci.yml ] && grep -q 'pr-checks.yml@' .github/workflows/ci.yml; then
    src="${NDPX_TEMPLATE:-$HERE/../ndpx-webapp-template}"; kind="web app template"; files="$WEB_FILES"
    [ -d "$src/.git" ] || die "template not found at $src. Run: cd /c/dev && git clone https://github.com/ndpx/ndpx-webapp-template"
  else
    src="$HERE"; kind="starter files"; files="$STARTER_FILES"
  fi
  git -C "$src" fetch -q origin main || die "cannot update $src; check your network and access."
  show() { if [ "$kind" = "starter files" ]; then git -C "$src" show "origin/main:starter/$1" 2>/dev/null; else git -C "$src" show "origin/main:$1" 2>/dev/null; fi; }
  # Production pin lines differ by design; compare deploy.yml as if both were on v1.
  unpin() { sed -E '/ndpx-production-pin/ s#(deploy\.yml@)[0-9a-f]{40}#\1v1#; /ndpx-production-pin/ s#(pipelines-ref: )[0-9a-f]{40}#\1v1#' | tr -d '\r'; }
  grep -q 'deploy\.yml@[0-9a-f]\{40\} # ndpx-production-pin' .github/workflows/deploy.yml 2>/dev/null && pinned=1
  say "Comparing with the latest $kind"
  for f in $files; do
    want="$(show "$f" | unpin)" || true
    [ -n "$want" ] || continue
    if [ ! -f "$f" ]; then state="missing"
    else have="$(unpin < "$f")"; [ "$have" = "$want" ] && state="same" || state="differs"; fi
    printf '   %-8s %s\n' "$state" "$f"
    if [ "$state" != "same" ]; then
      changed=1
      if [ $apply -eq 1 ]; then mkdir -p "$(dirname "$f")"; show "$f" > "$f"; fi
    fi
  done
  if grep -q 'ndpx:git-workflow:start' AGENTS.md 2>/dev/null; then
    awk '/ndpx:git-workflow:start/,/ndpx:git-workflow:end/' AGENTS.md | tr -d '\r' | cmp -s - <(tr -d '\r' < "$HERE/templates/git-workflow.md") \
      || echo "   differs  AGENTS.md Git workflow rules (fix with: ndpx rules)"
  else
    echo "   missing  AGENTS.md Git workflow rules (fix with: ndpx rules)"
  fi
  if [ $changed -eq 0 ]; then say "Shared files match the $kind."; return 0; fi
  if [ $apply -eq 0 ]; then
    say "Review what differs: ndpx sync --apply copies the latest versions; git diff shows the change; ndpx ship sends it for review."
    return 0
  fi
  if [ $pinned -eq 1 ]; then cmd_pin >/dev/null; say "Production pin set to the current v1 again."; fi
  git --no-pager diff --stat
  say "Copied. Check git diff for anything this repository had changed on purpose, then: ndpx ship \"Sync shared files\""
}

cmd_install() {
  local line="alias ndpx='bash \"$HERE/tools/ndpx.sh\"'"
  touch ~/.bashrc
  grep -qF "tools/ndpx.sh" ~/.bashrc || echo "$line" >> ~/.bashrc
  say "Installed. Close and reopen Git Bash, then type: ndpx"
}

case "${1:-}" in
  start) shift; cmd_start "$@" ;;
  check) shift; cmd_check ;;
  ship) shift; cmd_ship "$@" ;;
  done) shift; cmd_done ;;
  status) cmd_status ;;
  rules) cmd_rules ;;
  pin) cmd_pin ;;
  sync) shift; cmd_sync "$@" ;;
  install) cmd_install ;;
  *) usage ;;
esac
