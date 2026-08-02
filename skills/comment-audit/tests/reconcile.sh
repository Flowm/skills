#!/usr/bin/env bash
# The line-count cross-check: comment-line delta against the diff's
# non-blank delta, including the false alarm it is known to have.
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
new_repo

cat > a.ts <<'EOF'
// Offset applied before formatting. Restates the field below, which is
// exactly the sort of thing this audit removes.
export const offset = 0;

// Kept: the upstream API caps retries at three, so raising this does nothing.
export const retries = 3;
EOF
printf '# Restated comment.\nVALUE = 1\n' > b.py
commit_all init

run() { "$RECONCILE" HEAD >"$OUTDIR/out" 2>&1; echo $?; }

echo "-- a comment-only cleanup reconciles --"
cat > a.ts <<'EOF'
export const offset = 0;

// The upstream API caps retries at three, so raising this does nothing.
export const retries = 3;
EOF
check "exits 0" "0" "$(run)"
check "reports the deltas as equal" "1" "$(grep -c '^OK:' "$OUTDIR/out")"
check "  comment lines fell by two" "1" \
      "$(grep -c '^comment lines .* delta -2$' "$OUTDIR/out")"
git checkout -q .

echo "-- an added line of code is caught --"
printf '\nexport const SNEAKY = 1;\n' >> a.ts
check "exits 1" "1" "$(run)"
check "reports a mismatch" "1" "$(grep -c '^MISMATCH' "$OUTDIR/out")"
git checkout -q .

echo "-- a deleted line of code is caught --"
perl -ni -e 'print unless /export const retries/' a.ts
check "exits 1" "1" "$(run)"
check "reports a mismatch" "1" "$(grep -c '^MISMATCH' "$OUTDIR/out")"
git checkout -q .

echo "-- a MODIFIED code line nets to zero and slips through (documented) --"
# One deletion plus one insertion cancels, so the counts still agree. This is
# why the reconciliation complements `git diff -I` rather than replacing it.
cat > a.ts <<'EOF'
export const offset = 0;

// The upstream API caps retries at three.
export const retries = 5;
EOF
check "reconciliation does not catch it" "0" "$(run)"
check "  but the comment-only filter does" "reported" \
      "$([ -n "$(git diff --stat -I"$FILTER" -- .)" ] && echo reported || echo silent)"
git checkout -q .

echo "-- scope applies to the diff too, not just the comment count --"
# A code change outside the scope must not make a scoped run disagree.
printf '# Comment reworded.\nVALUE = 1\n' > b.py
printf '\nexport const OUT_OF_SCOPE = 1;\n' >> a.ts
check "scoped to py, the ts code change is ignored" "0" \
      "$("$RECONCILE" HEAD --ext py >/dev/null 2>&1; echo $?)"
check "unscoped, the same change is caught" "1" \
      "$("$RECONCILE" HEAD >/dev/null 2>&1; echo $?)"
check "scoping by path behaves the same" "0" \
      "$("$RECONCILE" HEAD b.py >/dev/null 2>&1; echo $?)"
git checkout -q .

echo "-- a removed line of content that begins with --- is still counted --"
# `---` is a document separator in YAML, not a comment. Its diff line reads
# `----`, which a prefix test mistakes for a file header.
printf -- '---\n# A comment.\nkey: value\n' > doc.yaml
commit_all "add doc.yaml"
perl -ni -e 'print unless /^---$/' doc.yaml
check "deleting it is reported as a code change" "1" "$(run)"
git checkout -q .

echo "-- the documented false alarm --"
# Removing a trailing comment shortens a line that stays in the file: the
# diff's non-blank net is zero while the comment count drops by one.
printf 'export const x = 1; // restates the name\n' > c.ts
commit_all "add a trailing comment"
printf 'export const x = 1;\n' > c.ts
check "trailing-comment removal reports a mismatch" "1" "$(run)"
check "  and the mismatch is exactly one line" "1" \
      "$(grep -c 'MISMATCH of 1 line' "$OUTDIR/out")"
git checkout -q .

echo "-- read-only --"
# The base state is read with `git show`, so nothing is checked out and
# uncommitted work is safe. Prove it with dirty state in the tree.
printf '// Uncommitted edit.\nexport const dirty = 1;\n' > dirty.ts
run >/dev/null
check "never creates a worktree" "1" "$(git worktree list | grep -c .)"
check "leaves the uncommitted file in place" "1" "$([ -f dirty.ts ] && echo 1 || echo 0)"
check "does not move HEAD" "1" \
      "$(git rev-parse --abbrev-ref HEAD | grep -c '^main$')"
rm -f dirty.ts

echo "-- error paths --"
check "an unknown ref exits 1" "1" "$("$RECONCILE" nope >/dev/null 2>&1; echo $?)"

finish
