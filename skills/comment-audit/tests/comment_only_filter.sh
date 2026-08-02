#!/usr/bin/env bash
# The Phase 2 safety net: `git diff -I` must stay silent for comment-only
# edits and must never hide a code change.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
new_repo

verify() {  # verify <label> <silent|reported> [extra -I args...]
  local label=$1 expect=$2; shift 2
  local out got
  out="$(git diff --stat -I"$FILTER" "$@" -- .)"
  got=silent; [ -n "$out" ] && got=reported
  check "$label" "$expect" "$got"
  git checkout -q . 2>/dev/null
}

cat > a.ts <<'EOF'
class Clock {
  // Offset applied before formatting.
  #offset = 0;
  #calibrated = false;
}
EOF
cat > b.css <<'EOF'
/* Palette shared with the theme switcher */
#app {
  --main-color: red;
}
EOF
cat > c.vue <<'EOF'
<template>
  <!-- Legend -->
  <div id="root">x</div>
</template>
EOF
cat > d.js <<'EOF'
/*
   Block body with no leading star
*/
const x = 1;
EOF
cat > e.py <<'EOF'
# Offset applied before formatting.
OFFSET = 0
EOF
commit_all

echo "-- comment-only edits stay silent --"
perl -pi -e 's{// Offset applied before formatting.}{// Offset in seconds.}' a.ts
verify "TS line comment reworded" silent
perl -pi -e 's{/\* Palette shared with the theme switcher \*/}{/* Palette. */}' b.css
verify "CSS block comment reworded" silent
perl -ni -e 'print unless m{<!-- Legend -->}' c.vue
verify "Vue HTML comment deleted" silent

echo "-- code changes are always reported --"
perl -pi -e 's{\#offset = 0;}{#offset = 3600;}' a.ts
verify "TS private field value changed" reported
perl -ni -e 'print unless /\#calibrated/' a.ts
verify "TS private field deleted" reported
perl -pi -e 's{^\#app\b}{#main}' b.css
verify "CSS id selector renamed" reported
perl -pi -e 's{--main-color: red;}{--main-color: blue;}' b.css
verify "CSS custom property changed" reported
perl -pi -e 's{<div id="root">x</div>}{<div id="root">y</div>}' c.vue
verify "Vue markup changed" reported
perl -pi -e 's{// Offset applied before formatting.}{// Offset.}; s{\#offset = 0;}{#offset = 42;}' a.ts
verify "comment and code in one hunk" reported
perl -pi -e 's{Block body with no leading star}{Block body, reworded}' d.js
verify "block-comment body (safe false positive)" reported

echo "-- Python paths need their own token, and only there --"
perl -pi -e 's{\# Offset applied before formatting.}{# Offset in seconds.}' e.py
verify "PY comment reworded" silent -I"$FILTER_PY"
perl -pi -e 's{OFFSET = 0}{OFFSET = 3600}' e.py
verify "PY code changed" reported -I"$FILTER_PY"

echo "-- the empty-match footgun --"
perl -pi -e 's{\#offset = 0;}{#offset = 99;}' a.ts
check "an empty-matchable -I hides a real code change" "" \
      "$(git diff --stat -I"$FILTER" -I'^[[:space:]]*$' -- .)"
check "  ...which the documented pattern does not" "reported" \
      "$([ -n "$(git diff --stat -I"$FILTER" -- .)" ] && echo reported || echo silent)"
git checkout -q .

echo "-- blank lines are deliberately unfiltered (documented noise) --"
printf '// Standalone comment.\n\nconst y = 2;\n' > f.ts
commit_all "add f.ts"
printf 'const y = 2;\n' > f.ts
verify "comment plus its blank line is reported, not silent" reported

finish
