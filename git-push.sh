#!/usr/bin/env bash
# =============================================================================
# git-push.sh — Stage, commit, and push all local changes to the remote.
#
# Usage:
#   ./git-push.sh                        # prompts for a commit message
#   ./git-push.sh "feat: add new widget" # uses the supplied message directly
#
# Compatible with: bash (Linux, macOS, Git Bash on Windows)
# =============================================================================

set -euo pipefail   # exit on error, undefined var, or pipe failure

# ---------------------------------------------------------------------------
# Colour helpers (gracefully degrade when not in a terminal)
# ---------------------------------------------------------------------------
if [ -t 1 ]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; CYAN=''; BOLD=''; RESET=''
fi

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# 1. Verify we are inside a Git repository
# ---------------------------------------------------------------------------
info "Checking Git repository..."
git rev-parse --git-dir > /dev/null 2>&1 \
  || die "Not inside a Git repository. Aborting."
success "Git repository found."

# ---------------------------------------------------------------------------
# 2. Detect the current branch name
# ---------------------------------------------------------------------------
BRANCH=$(git branch --show-current 2>/dev/null)
# Fallback for older Git versions that don't support --show-current
[ -z "$BRANCH" ] && BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
[ -z "$BRANCH" ] && die "Could not determine current branch name. Are you in a detached HEAD state?"

info "Current branch: ${BOLD}${BRANCH}${RESET}"

# ---------------------------------------------------------------------------
# 3. Check for uncommitted changes (staged + unstaged + untracked)
# ---------------------------------------------------------------------------
# git status --porcelain prints one line per changed/untracked file.
# An empty output means the working tree is completely clean.
PORCELAIN=$(git status --porcelain 2>/dev/null)

if [ -z "$PORCELAIN" ]; then
  warn "Working tree is clean — nothing to stage or commit."
  # Still try to push in case local branch is ahead of remote
  info "Attempting to push any already-committed but un-pushed commits..."
else
  # Show a human-friendly summary of what will be staged
  echo ""
  info "Changes detected:"
  git status --short
  echo ""

  # -------------------------------------------------------------------------
  # 4. Stage all changes (modified, new, deleted)
  # -------------------------------------------------------------------------
  info "Staging all changes with 'git add -A'..."
  git add -A
  success "All changes staged."

  # -------------------------------------------------------------------------
  # 5. Determine the commit message
  # -------------------------------------------------------------------------
  if [ -n "${1:-}" ]; then
    # Message supplied as first argument — use it directly
    COMMIT_MSG="$1"
    info "Using provided commit message: \"${COMMIT_MSG}\""
  else
    # Interactive prompt with a sensible default
    DEFAULT_MSG="chore: update project files on branch ${BRANCH}"
    echo -e "${YELLOW}Enter commit message${RESET} (press Enter to use default):"
    echo -e "  Default: ${BOLD}${DEFAULT_MSG}${RESET}"
    printf "> "
    read -r USER_MSG
    COMMIT_MSG="${USER_MSG:-$DEFAULT_MSG}"
  fi

  [ -z "$COMMIT_MSG" ] && die "Commit message cannot be empty."

  # -------------------------------------------------------------------------
  # 6. Create the commit
  # -------------------------------------------------------------------------
  info "Committing with message: \"${COMMIT_MSG}\""
  if git commit -m "$COMMIT_MSG"; then
    success "Commit created successfully."
  else
    # git commit exits non-zero when there's nothing to commit (e.g. if
    # git add -A staged nothing because everything was already staged and
    # then rolled back).  Treat this as a warning, not a fatal error.
    warn "'git commit' returned a non-zero exit code."
    warn "This can happen if the index was already clean after staging."
  fi
fi

# ---------------------------------------------------------------------------
# 7. Push to the remote
#    • First attempt a normal push.
#    • If the remote tracking branch doesn't exist yet, retry with
#      --set-upstream so Git creates it automatically.
#    • If the push is rejected (e.g. remote has diverged), print guidance
#      instead of silently failing.
# ---------------------------------------------------------------------------
info "Pushing branch '${BRANCH}' to origin..."

PUSH_OUTPUT=$(git push origin "$BRANCH" 2>&1) && PUSH_EXIT=0 || PUSH_EXIT=$?

if [ $PUSH_EXIT -eq 0 ]; then
  success "Push succeeded."
  echo "$PUSH_OUTPUT"
else
  # Check whether the failure is a missing remote-tracking ref
  if echo "$PUSH_OUTPUT" | grep -qiE "has no upstream|no upstream branch|set-upstream|--set-upstream"; then
    warn "Remote tracking branch not found. Retrying with --set-upstream..."
    git push --set-upstream origin "$BRANCH" \
      && success "Push succeeded (upstream set to origin/${BRANCH})." \
      || die "Push with --set-upstream also failed. See the error above."

  # Check for a rejection due to non-fast-forward (diverged history)
  elif echo "$PUSH_OUTPUT" | grep -qiE "rejected|non-fast-forward|fetch first"; then
    echo ""
    error "Push was REJECTED. The remote branch has commits your local branch does not."
    echo ""
    echo -e "${YELLOW}How to resolve:${RESET}"
    echo "  Option A — rebase your local changes on top of the remote:"
    echo "    git pull --rebase origin ${BRANCH}"
    echo "    ./git-push.sh  # then re-run this script"
    echo ""
    echo "  Option B — merge the remote changes into your local branch:"
    echo "    git pull origin ${BRANCH}"
    echo "    # resolve any merge conflicts, then re-run this script"
    echo ""
    echo "  Option C — force-push (DANGEROUS — rewrites remote history):"
    echo "    git push --force-with-lease origin ${BRANCH}"
    echo ""
    die "Aborted due to push rejection."

  # Merge conflict indicators in the output
  elif echo "$PUSH_OUTPUT" | grep -qiE "conflict|CONFLICT"; then
    error "Merge conflict detected."
    echo -e "${YELLOW}Resolve all conflicts, stage the resolved files, and re-run this script.${RESET}"
    die "Aborted due to merge conflict."

  else
    # Unknown error — surface the raw Git output and bail out
    error "git push failed with exit code ${PUSH_EXIT}:"
    echo "$PUSH_OUTPUT" >&2
    die "Push failed. See the error output above."
  fi
fi

# ---------------------------------------------------------------------------
# 8. Final summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}${BOLD}========================================${RESET}"
echo -e "${GREEN}${BOLD}  All done! Changes pushed to remote.  ${RESET}"
echo -e "${GREEN}${BOLD}========================================${RESET}"
echo ""
echo -e "  Branch : ${BOLD}${BRANCH}${RESET}"
echo -e "  Remote : ${BOLD}origin/${BRANCH}${RESET}"
echo ""
