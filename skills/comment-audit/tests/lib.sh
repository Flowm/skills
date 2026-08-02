# Shared helpers for the comment-audit suites. Source, don't execute.
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXTRACT="$SKILL_DIR/scripts/extract_comments.py"
RECONCILE="$SKILL_DIR/scripts/reconcile_comment_lines.py"

# The comment-only filter documented in SKILL.md. Keep the two in step: this
# pattern is the safety net for every Phase 2 commit.
FILTER="^[[:space:]]*(//|/\*|\*|<!--|-->)"
FILTER_PY="^[[:space:]]*#"

PASSED=0
FAILED=0

pass() { PASSED=$((PASSED + 1)); printf '  PASS  %s\n' "$1"; }
fail() {
  FAILED=$((FAILED + 1))
  printf '  FAIL  %s\n' "$1"
  [ $# -gt 1 ] && printf '        %s\n' "$2"
  return 0
}

check() {  # check <label> <expected> <actual>
  if [ "$2" = "$3" ]; then pass "$1"; else fail "$1" "expected [$2], got [$3]"; fi
}

# A scratch git repository, removed when the suite exits.
new_repo() {
  WORK="$(mktemp -d)"
  # Scratch output must live outside the repo: a suite that writes into its
  # own fixture would see that file in the diffs it is measuring.
  OUTDIR="$(mktemp -d)"
  trap 'rm -rf "$WORK" "$OUTDIR"' EXIT
  cd "$WORK" || exit 1
  git init -qb main .
  git config user.email test@example.invalid
  git config user.name "Comment Audit Tests"
}

commit_all() { git add -A && git commit -qm "${1:-checkpoint}"; }

finish() {
  printf '\n%s: %d passed, %d failed\n' "$(basename "$0")" "$PASSED" "$FAILED"
  [ "$FAILED" -eq 0 ]
}
