#!/usr/bin/env bash
# Extraction correctness: which lines count as comments, and the output modes.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
new_repo

cat > a.css <<'EOF'
/* Palette shared with the theme switcher */
#app {
  --main-color: red;
}
EOF
cat > b.rs <<'EOF'
// Real comment
#[derive(Debug, Clone)]
pub struct Foo {}
EOF
cat > c.js <<'EOF'
/*
   Block body with no leading star
   second line
*/
const x = 1;
EOF
cat > d.ts <<'EOF'
const a = 1; // see https://github.com/foo/bar/issues/42 for why
class Clock {
  #offset = 0;
}
EOF
cat > e.py <<'EOF'
# Real python comment
x = 1  # trailing python comment
EOF
commit_all

echo "-- '#' is code outside the Python family --"
out="$("$EXTRACT" 2>&1)"
check "no CSS id selector as comment text" "" \
      "$(printf '%s\n' "$out" | grep -F '| #app' || true)"
check "no Rust attribute as comment text" "" \
      "$(printf '%s\n' "$out" | grep -F '| #[derive' || true)"
check "no TS private field as comment text" "" \
      "$(printf '%s\n' "$out" | grep -F '| #offset' || true)"
check "CSS selector is reported as the code line" "1" \
      "$(printf '%s\n' "$out" | grep -cF '> #app {')"
check "Python '#' still opens a comment" "1" \
      "$(printf '%s\n' "$out" | grep -cF '| # Real python comment')"

echo "-- block comments run to their closing delimiter --"
check "body line without a leading star is captured" "1" \
      "$(printf '%s\n' "$out" | grep -cF '| Block body with no leading star')"
check "closing delimiter is captured" "1" \
      "$(printf '%s\n' "$out" | grep -cxF '    | */')"
check "the line after the block is the code line" "1" \
      "$(printf '%s\n' "$out" | grep -cF '> const x = 1;')"

echo "-- trailing comments --"
check "a URL in a trailing comment does not drop it" "1" \
      "$(printf '%s\n' "$out" | grep -cF 'TRAILING: // see https://github.com')"
check "trailing Python comment found" "1" \
      "$(printf '%s\n' "$out" | grep -cF 'TRAILING: # trailing python comment')"

echo "-- output modes --"
check "--count blocks matches the text listing" \
      "$(printf '%s\n' "$out" | grep -c '^  L')" \
      "$("$EXTRACT" --count | sed -n 's/^blocks=//p')"
check "--count comment_lines matches the text listing" \
      "$(printf '%s\n' "$out" | grep -c '^    | ')" \
      "$("$EXTRACT" --count | sed -n 's/^comment_lines=//p')"
check "--tsv emits one row per block" \
      "$(printf '%s\n' "$out" | grep -c '^  L')" \
      "$("$EXTRACT" --tsv | grep -c .)"
check "--tsv rows have five columns" "1" \
      "$("$EXTRACT" --tsv | awk -F'\t' '{print NF}' | sort -u | tr -d '\n' | grep -c '^5$')"
check "--tsv and --count refuse to combine" "2" \
      "$("$EXTRACT" --tsv --count >/dev/null 2>&1; echo $?)"

echo "-- --rev reads a commit without checking anything out --"
printf '// Added after the baseline.\nexport const later = 1;\n' > g.ts
BASELINE="$(git rev-parse HEAD)"
commit_all "add g.ts"
check "--rev sees the old state" "0" \
      "$("$EXTRACT" --rev "$BASELINE" | grep -c 'Added after the baseline')"
check "the working tree still sees the new state" "1" \
      "$("$EXTRACT" | grep -c 'Added after the baseline')"
check "--rev output matches the working tree at the same commit" \
      "$("$EXTRACT" --count | sed -n 's/^comment_lines=//p')" \
      "$("$EXTRACT" --rev HEAD --count | sed -n 's/^comment_lines=//p')"
check "--rev combines with --tsv" "1" \
      "$("$EXTRACT" --rev HEAD --tsv | grep -c 'Added after the baseline')"
check "--rev rejects an unknown commit" "1" \
      "$("$EXTRACT" --rev nope >/dev/null 2>&1; echo $?)"
check "--rev and --since refuse to combine" "2" \
      "$("$EXTRACT" --rev HEAD --since main >/dev/null 2>&1; echo $?)"
check "--rev creates no worktree" "1" "$(git worktree list | grep -c .)"
check "--rev leaves the tree clean" "0" "$(git status --porcelain | grep -c .)"

echo "-- scope and error paths --"
check "--ext filters" "==== e.py" "$("$EXTRACT" --ext py | grep '^====')"
check "unknown --ext exits 1" "1" "$("$EXTRACT" --ext bogus >/dev/null 2>&1; echo $?)"
check "unknown --ext names the flag's known set" "1" \
      "$("$EXTRACT" --ext bogus 2>&1 | grep -c 'known:')"
check "--exclude drops a path" "" "$("$EXTRACT" --ext py --exclude e.py 2>/dev/null || true)"
check "no match exits 1" "1" "$("$EXTRACT" --ext py --exclude e.py >/dev/null 2>&1; echo $?)"

finish
