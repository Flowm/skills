#!/usr/bin/env python3
"""Extract comment blocks with their following code line, one section per file.

Prints, for every run of adjacent comment lines, the comment text and the next
non-blank line of code:

    ==== src/orbit.ts
      L42:
        | // Propagation drifts past ~30 days, so callers clamp the window.
        > export function propagate(tle: Tle, until: Date) {

Scope defaults to every tracked file. Narrow it by path, by extension, or to
the files a branch has touched:

    scripts/extract_comments.py                        # whole repository
    scripts/extract_comments.py src/components         # one directory
    scripts/extract_comments.py --ext ts,vue           # only these extensions
    scripts/extract_comments.py --exclude vendor,gen   # skip matching paths
    scripts/extract_comments.py --since main           # files this branch changed
    scripts/extract_comments.py --since main src/api   # both
    scripts/extract_comments.py --rev main             # as of a commit

With --since, blocks the branch actually added or edited are marked [branch];
the rest are pre-existing comments in the same files.

--rev reads file contents out of a commit with `git show`. Nothing is checked
out and the working tree is never touched, so it is safe to run against a repo
with uncommitted work in it.
"""
import argparse
import re
import subprocess
import sys

# (line-comment tokens, block-comment delimiter pairs) per extension.
#
# Tokens are per language on purpose. `#` opens a comment only in the Python
# family; elsewhere it is code — a TypeScript private field (`#offset = 0`), a
# CSS id selector (`#app {`), a Rust attribute (`#[derive(...)]`). One union
# pattern across all languages hands those to the classifier as comment text
# and merges them into the block above, so a "delete this block" edit takes a
# code line with it.
LANGS = {
    ("ts", "tsx", "js", "jsx", "mjs", "cjs", "go", "rs", "java", "kt", "swift",
     "c", "h", "cc", "cpp", "hpp", "cs", "scala", "php"):
        (("//",), (("/*", "*/"),)),
    ("py", "rb", "sh", "bash", "zsh", "yaml", "yml", "toml"):
        (("#",), ()),
    ("css", "scss", "less"): ((), (("/*", "*/"),)),
    ("html", "xml", "svg", "md"): ((), (("<!--", "-->"),)),
    ("vue", "svelte", "astro"): (("//",), (("/*", "*/"), ("<!--", "-->"))),
    ("sql",): (("--",), (("/*", "*/"),)),
    ("lua",): (("--",), (("--[[", "]]"),)),
}
TOKENS = {ext: toks for exts, toks in LANGS.items() for ext in exts}

# Comment bodies are printed in full up to this width; the code line is a
# locator, not the subject, so it is cut shorter.
COMMENT_WIDTH = 200
CODE_WIDTH = 150

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(*args, fatal=True):
    try:
        done = subprocess.run(("git",) + args, capture_output=True, text=True,
                              check=True)
    except FileNotFoundError:
        sys.exit("git not found on PATH")
    except subprocess.CalledProcessError as err:
        if not fatal:
            return None
        sys.exit(f"git {' '.join(args)} failed: {err.stderr.strip() or err}")
    return done.stdout


def merge_base(ref):
    """Fork point of ref and HEAD, so a branch scope excludes the base's own
    commits. Comparing against that point rather than the ref itself also
    picks up uncommitted work, which is usually what a mid-cleanup audit wants.
    """
    base = git("merge-base", ref, "HEAD", fatal=False)
    if base is None:
        sys.exit(f"cannot find a merge base between '{ref}' and HEAD — check "
                 f"that '{ref}' is a valid branch, tag, or commit")
    return base.strip()


def select_files(exts, excludes, paths, base, rev):
    if rev:
        listing = git("ls-tree", "-r", "-z", "--name-only", rev, "--", *paths)
    elif base:
        # Lowercase d excludes deletions: a file the branch removed has no
        # comments left to audit.
        listing = git("diff", "--name-only", "-z", "--diff-filter=d", base,
                      "--", *paths)
    else:
        listing = git("ls-files", "-z", "--", *paths)
    files = []
    # NUL-separated: git quotes and escapes paths with non-ASCII or special
    # characters in its line-based output, and the escaped form does not open.
    for path in listing.split("\0"):
        if not path:
            continue
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        if ext not in exts:
            continue
        if any(skip in path for skip in excludes):
            continue
        files.append(path)
    return files


def file_lines(path, rev):
    """Contents of path, from the working tree or straight out of a commit.

    Reading a revision through `git show` rather than checking it out keeps
    this read-only: no temporary worktree to create and delete, and nothing
    that can disturb the tree the user is editing.
    """
    if rev is None:
        with open(path, encoding="utf-8") as handle:
            return handle.read().split("\n")
    blob = git("show", f"{rev}:{path}", fatal=False)
    if blob is None:
        raise OSError(f"cannot read from {rev}")
    return blob.split("\n")


def changed_lines(base, path):
    """Line numbers this branch added or edited in path, on the new side."""
    diff = git("diff", "--unified=0", base, "--", path, fatal=False)
    if not diff:
        return set()
    touched = set()
    for line in diff.splitlines():
        match = HUNK.match(line)
        if not match:
            continue
        start = int(match.group(1))
        count = 1 if match.group(2) is None else int(match.group(2))
        # count == 0 marks a pure deletion; it adds no new lines to review.
        touched.update(range(start, start + count))
    return touched


def comment_block(lines, i, line_toks, block_toks):
    """Consume a run of adjacent comment lines starting at i.

    Block comments are followed through to their closing delimiter: a body line
    that does not begin with `*` is still comment text, and treating it as the
    next code line means classifying a comment nobody read.
    """
    block = []
    while i < len(lines):
        stripped = lines[i].strip()
        opener = next((b for b in block_toks if stripped.startswith(b[0])), None)
        if opener:
            block.append(stripped)
            if opener[1] not in stripped[len(opener[0]):]:
                while i + 1 < len(lines) and opener[1] not in lines[i]:
                    i += 1
                    block.append(lines[i].strip())
            i += 1
        elif any(stripped.startswith(t) for t in line_toks):
            block.append(stripped)
            i += 1
        else:
            break
    return block, i


def trailing_comment(line, line_toks):
    for tok in line_toks:
        match = re.search(rf"\S\s+({re.escape(tok)}.*)$", line)
        if match:
            return match.group(1)
    return None


def scan(lines, line_toks, block_toks, touched):
    """Yield one record per comment block: (line, body, code, is_branch)."""

    def branch(first, last):
        return touched is not None and any(n in touched
                                           for n in range(first, last + 1))

    i = 0
    while i < len(lines):
        start = i
        block, i = comment_block(lines, i, line_toks, block_toks)
        if block:
            code = i
            while code < len(lines) and not lines[code].strip():
                code += 1
            following = lines[code].strip() if code < len(lines) else ""
            yield start + 1, block, following, branch(start + 1, i)
            continue
        trailing = trailing_comment(lines[i], line_toks)
        if trailing:
            yield (i + 1, ["TRAILING: " + trailing], lines[i].strip(),
                   branch(i + 1, i + 1))
        i += 1


def report(path, line_toks, block_toks, touched, fmt, rev):
    """Print one file's comments. Returns (blocks, comment_lines, marked)."""
    try:
        records = list(scan(file_lines(path, rev), line_toks, block_toks, touched))
    except (OSError, UnicodeDecodeError) as err:
        if fmt == "text":
            print(f"==== {path}\n  (skipped: {err})")
        else:
            print(f"# skipped {path}: {err}", file=sys.stderr)
        return 0, 0, 0

    if fmt == "text":
        print(f"==== {path}")
    blocks = comment_lines = marked = 0
    for line, body, code, is_branch in records:
        blocks += 1
        comment_lines += len(body)
        marked += is_branch
        if fmt == "text":
            print(f"  L{line}:{' [branch]' if is_branch else ''}")
            for entry in body:
                print("    | " + entry[:COMMENT_WIDTH])
            print("    > " + code[:CODE_WIDTH])
        elif fmt == "tsv":
            # One row per block so the report table is generated rather than
            # retyped; tabs and newlines in the source would break the columns.
            joined = " ".join(body).replace("\t", " ")
            print("\t".join((path, str(line), "branch" if is_branch else "",
                             joined[:COMMENT_WIDTH],
                             code[:CODE_WIDTH].replace("\t", " "))))
    return blocks, comment_lines, marked


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="limit to these directories or files (git pathspecs)")
    parser.add_argument("--ext", help="comma-separated extensions to include "
                                      "(default: every extension in LANGS)")
    parser.add_argument("--exclude", default="",
                        help="comma-separated substrings; paths containing one are skipped")
    parser.add_argument("--since", metavar="REF",
                        help="only files changed since the merge base of REF and "
                             "HEAD, including uncommitted work")
    parser.add_argument("--rev", metavar="REV",
                        help="read files as of REV instead of the working tree; "
                             "reads through git, never checks anything out")
    parser.add_argument("--tsv", action="store_true",
                        help="emit file/line/branch/comment/code columns instead of "
                             "the readable listing, to build the report table from")
    parser.add_argument("--count", action="store_true",
                        help="print totals only, for reconciling against a diff")
    args = parser.parse_args()
    if args.tsv and args.count:
        parser.error("--tsv and --count are mutually exclusive")
    if args.rev and args.since:
        parser.error("--rev and --since are mutually exclusive: --since marks "
                     "lines against the working tree")
    rev = None
    if args.rev:
        rev = (git("rev-parse", "--verify", f"{args.rev}^{{commit}}", fatal=False)
               or "").strip()
        if not rev:
            sys.exit(f"not a commit: {args.rev}")
    fmt = "tsv" if args.tsv else "count" if args.count else "text"

    if args.ext:
        exts = [e.strip().lstrip(".") for e in args.ext.split(",") if e.strip()]
        unknown = [e for e in exts if e not in TOKENS]
        if unknown:
            sys.exit(f"no comment tokens defined for: {', '.join(unknown)}\n"
                     f"known: {', '.join(sorted(TOKENS))}\n"
                     f"add an entry to LANGS in {__file__}")
        exts = set(exts)
    else:
        exts = set(TOKENS)

    excludes = [x.strip() for x in args.exclude.split(",") if x.strip()]
    base = merge_base(args.since) if args.since else None
    files = select_files(exts, excludes, args.paths, base, rev)
    if not files:
        scope = (f"changed since {args.since}" if args.since
                 else f"tracked at {args.rev}" if rev else "tracked by git")
        sys.exit(f"no files matched (scope: {scope}) — check the PATH arguments, "
                 f"--ext, and --exclude, and that this is a git repository")

    blocks = comment_lines = marked = 0
    for path in files:
        line_toks, block_toks = TOKENS[path.rsplit(".", 1)[-1]]
        counts = report(path, line_toks, block_toks,
                        changed_lines(base, path) if base else None, fmt, rev)
        blocks += counts[0]
        comment_lines += counts[1]
        marked += counts[2]

    if fmt == "count":
        print(f"files={len(files)}")
        print(f"blocks={blocks}")
        print(f"comment_lines={comment_lines}")
        if base:
            print(f"marked={marked}")
    elif fmt == "text" and base:
        # The trailer avoids both the `====` file prefix and a literal
        # "[branch]": counting either with grep would otherwise pick it up.
        print(f"\n---- scope: {len(files)} file(s) changed since {args.since}, "
              f"{marked} comment block(s) marked")


if __name__ == "__main__":
    main()
