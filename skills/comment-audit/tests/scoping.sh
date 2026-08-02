#!/usr/bin/env bash
# Path and branch scoping, including what --since can and cannot see.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
new_repo

mkdir -p src/api src/ui
printf '// Pre-existing comment in api.\nexport const base = "/v1";\n' > src/api/client.ts
printf '// Pre-existing comment in ui.\nexport const width = 100;\n' > src/ui/panel.ts
printf '<!-- Pre-existing markdown comment -->\n# Title\n' > README.md
commit_all init

# main moves on after the branch forks; those commits must stay out of scope.
git checkout -qb feature
git checkout -q main
printf '// Comment added on main after the fork.\nexport const old = true;\n' > src/api/legacy.ts
commit_all "main advances"
git checkout -q feature
cat > src/api/client.ts <<'EOF'
// Pre-existing comment in api.
export const base = "/v1";

// Added by the branch: retries are capped upstream.
export function get(path: string) {}
EOF
commit_all "branch work"
printf '\n// Uncommitted comment in ui.\nexport const height = 50;\n' >> src/ui/panel.ts

echo "-- path scoping --"
check "no arguments covers the whole repo" "3" "$("$EXTRACT" | grep -c '^====')"
check "a directory scopes to its files" "==== src/ui/panel.ts" "$("$EXTRACT" src/ui | grep '^====')"
check "several pathspecs combine" "2" "$("$EXTRACT" src/api README.md | grep -c '^====')"
check "paths combine with --ext" "1" "$("$EXTRACT" src --ext ts | grep -c 'Pre-existing comment in api')"

echo "-- branch scoping --"
out="$("$EXTRACT" --since main)"
check "excludes files main added after the fork" "0" \
      "$(printf '%s\n' "$out" | grep -c 'legacy.ts')"
check "includes committed branch work" "1" \
      "$(printf '%s\n' "$out" | grep -c 'Added by the branch')"
check "includes uncommitted work" "1" \
      "$(printf '%s\n' "$out" | grep -c 'Uncommitted comment in ui')"
check "marks blocks the branch touched" "2" \
      "$(printf '%s\n' "$out" | grep -c '^  L.*\[branch\]')"
check "leaves untouched blocks in the same file unmarked" "1" \
      "$(printf '%s\n' "$out" | grep -A1 'Pre-existing comment in api' | grep -c 'Pre-existing')"
check "the trailer does not use the '====' file prefix" "2" \
      "$(printf '%s\n' "$out" | grep -c '^====')"
check "the trailer counts files and marks" "1" \
      "$(printf '%s\n' "$out" | grep -c '^---- scope: 2 file(s) changed since main, 2 comment')"
check "the trailer has no literal '[branch]' to miscount" "0" \
      "$(printf '%s\n' "$out" | grep '^----' | grep -c '\[branch\]')"
check "--since combines with a pathspec" "1" \
      "$("$EXTRACT" --since main src/api | grep -c '^====')"

echo "-- limits of --since --"
# A comment the branch deleted is gone from the working tree, so nothing can
# mark it. Auditing a cleanup someone else made needs the base state instead.
commit_all "keep the uncommitted work"
git rm -q src/ui/panel.ts && commit_all "branch deletes a file"
check "a deleted file is not listed" "0" \
      "$("$EXTRACT" --since main | grep -c 'panel.ts')"
check "its comments are unreachable from the working tree" "0" \
      "$("$EXTRACT" --since main | grep -c 'Pre-existing comment in ui')"

echo "-- error paths --"
check "an unknown ref exits 1" "1" "$("$EXTRACT" --since nope >/dev/null 2>&1; echo $?)"
check "an unknown ref says so" "1" \
      "$("$EXTRACT" --since nope 2>&1 | grep -c 'merge base')"
check "an empty scope exits 1" "1" \
      "$("$EXTRACT" --since main --ext md >/dev/null 2>&1; echo $?)"

finish
