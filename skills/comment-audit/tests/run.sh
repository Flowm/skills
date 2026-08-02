#!/usr/bin/env bash
# Run every comment-audit suite. Exits non-zero if any of them fails.
#
#   skills/comment-audit/tests/run.sh
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1

FAILED=()
for suite in extractor.sh comment_only_filter.sh scoping.sh reconcile.sh; do
  printf '\n=== %s\n' "$suite"
  bash "$suite" || FAILED+=("$suite")
done

printf '\n'
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "all suites passed"
else
  printf 'FAILED: %s\n' "${FAILED[*]}"
  exit 1
fi
