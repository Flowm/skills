#!/usr/bin/env python3
"""Cross-check the extractor's idea of a comment against the raw diff.

If every non-blank line the diff touched was a comment line, then

    (comment lines after) - (comment lines before)

equals the diff's net non-blank line change.

This catches lines that were ADDED or DELETED without being comments, and it
catches the extractor's token table being wrong for a language in scope — the
count comes from LANGS, the diff figure does not, so a bad token set makes
them disagree.

It does NOT catch a line that was MODIFIED. Editing `retries = 3` to
`retries = 5` is one deletion and one insertion, which nets to zero and
reconciles cleanly. `git diff -I` is what catches that, so run both; this is a
complement to the comment-only filter, never a replacement.

Known false alarm: removing a trailing comment shortens a line that stays in
the file, so the comment count drops by one while the diff's non-blank net
stays at zero. A mismatch of one or two is worth reading before believing.

Every operation here only reads. The base state comes out of git with
`git show`, so there is no checkout, no temporary worktree, and nothing that
can disturb uncommitted work.

Usage:
    scripts/reconcile_comment_lines.py                  # working tree vs HEAD
    scripts/reconcile_comment_lines.py main             # vs a branch
    scripts/reconcile_comment_lines.py main --ext ts,vue
    scripts/reconcile_comment_lines.py main src/domain

Scope flags take the same form as extract_comments.py and are applied to both
sides, so the two counts always cover the same files.
"""
import argparse
import sys

import extract_comments as extractor


def comment_lines(rev, exts, excludes, paths, seen):
    """Total comment lines at a revision, or in the working tree if rev is None.

    Records every file it counted in `seen`, so the diff can be held to the
    same scope.
    """
    total = 0
    for path in extractor.select_files(exts, excludes, paths, None, rev):
        seen.add(path)
        line_toks, block_toks = extractor.TOKENS[path.rsplit(".", 1)[-1]]
        try:
            lines = extractor.file_lines(path, rev)
        except (OSError, UnicodeDecodeError):
            # Same files the extractor would skip, so both sides stay comparable.
            continue
        for _line, body, _code, _branch in extractor.scan(lines, line_toks,
                                                          block_toks, None):
            total += len(body)
    return total


def diff_line_counts(base, wanted):
    """Non-blank lines added and removed between base and the working tree,
    counting only files in `wanted`.

    The scope has to match the one the comment count used, or the two figures
    describe different sets of files and disagree for no reason. Files are
    filtered here rather than with a pathspec so that scoping by extension and
    by path can both apply, and so the argument list stays short.

    Only lines inside hunks count. A file header is `---`/`+++` before the
    first `@@`, and a removed line of content may itself begin with `---`, so
    position distinguishes them, not the prefix.
    """
    added = removed = 0
    in_hunk = False
    old_path = current = None
    # core.quotePath=false keeps non-ASCII paths as UTF-8 instead of escapes,
    # so they compare equal to what git ls-files -z reported.
    diff = extractor.git("-c", "core.quotePath=false", "diff", "-U0", base)
    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            in_hunk, old_path, current = False, None, None
        elif line.startswith("--- "):
            old_path = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("+++ "):
            current = old_path if line == "+++ /dev/null" else line[6:]
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line and current in wanted:
            if line[0] == "+" and line[1:].strip():
                added += 1
            elif line[0] == "-" and line[1:].strip():
                removed += 1
    return added, removed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ref", nargs="?", default="HEAD", metavar="REF",
                        help="commit to compare against (default: HEAD)")
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="limit to these directories or files")
    parser.add_argument("--ext", help="comma-separated extensions to include")
    parser.add_argument("--exclude", default="",
                        help="comma-separated substrings; paths containing one are skipped")
    args = parser.parse_args()

    base = (extractor.git("rev-parse", "--verify", f"{args.ref}^{{commit}}",
                          fatal=False) or "").strip()
    if not base:
        sys.exit(f"not a commit: {args.ref}")

    if args.ext:
        exts = {e.strip().lstrip(".") for e in args.ext.split(",") if e.strip()}
        unknown = sorted(e for e in exts if e not in extractor.TOKENS)
        if unknown:
            sys.exit(f"no comment tokens defined for: {', '.join(unknown)}")
    else:
        exts = set(extractor.TOKENS)
    excludes = [x.strip() for x in args.exclude.split(",") if x.strip()]

    # The union of both sides: a file the cleanup added exists in one and not
    # the other, and its lines still belong in the comparison.
    scope = set()
    before = comment_lines(base, exts, excludes, args.paths, scope)
    after = comment_lines(None, exts, excludes, args.paths, scope)
    added, removed = diff_line_counts(base, scope)

    comment_delta = after - before
    diff_delta = added - removed
    print(f"comment lines   {before} -> {after}   delta {comment_delta}")
    print(f"diff non-blank  +{added} -{removed}   delta {diff_delta}")

    if comment_delta == diff_delta:
        print("OK: every non-blank line the diff touched is accounted for as a comment")
        return 0
    print(f"MISMATCH of {diff_delta - comment_delta} line(s): the diff changed")
    print("non-comment lines, or a trailing comment was removed. Read the diff.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
