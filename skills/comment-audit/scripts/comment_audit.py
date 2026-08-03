#!/usr/bin/env python3
"""The comment-audit tool. Three subcommands over one comment model:

  extract   every comment block with its following code line, per file
  verify    check that a cleanup diff is comment-only, two independent ways
  compare   the comments at two revisions, paired block by block

    scripts/comment_audit.py extract src/api --ext ts,vue
    scripts/comment_audit.py extract --since main
    scripts/comment_audit.py verify HEAD src/api
    scripts/comment_audit.py compare "$(git merge-base main HEAD)"

Every subcommand only reads: revision state comes out of `git show` and
`git diff`, nothing is checked out, and uncommitted work is never touched.
Scope arguments mean the same thing everywhere — positional paths are git
pathspecs, --ext filters by extension, --exclude by path substring.
"""
import argparse
import re
import subprocess
import sys
from collections import Counter, defaultdict

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


def resolve_commit(ref):
    """SHA of ref, or exit naming the bad reference."""
    sha = (git("rev-parse", "--verify", f"{ref}^{{commit}}", fatal=False)
           or "").strip()
    if not sha:
        sys.exit(f"not a commit: {ref}")
    return sha


# Every subcommand takes the same scope arguments so their semantics cannot
# drift: a path, extension, or exclusion means the same thing to all three.

def add_scope_args(parser):
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="limit to these directories or files (git pathspecs)")
    parser.add_argument("--ext", help="comma-separated extensions to include "
                                      "(default: every extension in LANGS)")
    parser.add_argument("--exclude", default="",
                        help="comma-separated substrings; paths containing one are skipped")


def scope_of(args):
    """(extensions, exclude substrings) from parsed scope arguments."""
    if args.ext:
        exts = {e.strip().lstrip(".") for e in args.ext.split(",") if e.strip()}
        unknown = sorted(e for e in exts if e not in TOKENS)
        if unknown:
            sys.exit(f"no comment tokens defined for: {', '.join(unknown)}\n"
                     f"known: {', '.join(sorted(TOKENS))}\n"
                     f"add an entry to LANGS in {__file__}")
    else:
        exts = set(TOKENS)
    excludes = [x.strip() for x in args.exclude.split(",") if x.strip()]
    return exts, excludes


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


def diff_body_lines(base, paths=()):
    """Yield (path, sign, text) for every +/- line in `git diff -U0 base`.

    Paths are reported on the new side, or the old side for a deletion. Only
    hunk body lines are yielded: a file header is `---`/`+++` before the
    first `@@`, and a removed content line may itself begin with `---`, so
    position distinguishes them, not the prefix.
    """
    in_hunk = False
    old_path = current = None
    # core.quotePath=false keeps non-ASCII paths as UTF-8 instead of escapes,
    # so they compare equal to what git ls-files -z reported.
    diff = git("-c", "core.quotePath=false", "diff", "-U0", base, "--", *paths)
    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            in_hunk, old_path, current = False, None, None
        elif line.startswith("--- "):
            old_path = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("+++ "):
            current = old_path if line == "+++ /dev/null" else line[6:]
        elif line.startswith("@@"):
            in_hunk = True
        elif in_hunk and line and current and line[0] in "+-":
            yield current, line[0], line[1:]


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


# ---------------------------------------------------------------- extract

EXTRACT_HELP = """\
Print, for every run of adjacent comment lines, the comment text and the next
non-blank line of code:

    ==== src/orbit.ts
      L42:
        | // Propagation drifts past ~30 days, so callers clamp the window.
        > export function propagate(tle: Tle, until: Date) {

With --since, blocks the branch actually added or edited are marked [branch];
the rest are pre-existing comments in the same files. --rev reads file
contents out of a commit with `git show`; nothing is checked out.
"""


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


def cmd_extract(args):
    if args.tsv and args.count:
        args.parser.error("--tsv and --count are mutually exclusive")
    if args.rev and args.since:
        args.parser.error("--rev and --since are mutually exclusive: --since "
                          "marks lines against the working tree")
    rev = resolve_commit(args.rev) if args.rev else None
    fmt = "tsv" if args.tsv else "count" if args.count else "text"
    exts, excludes = scope_of(args)
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
    return 0


# ----------------------------------------------------------------- verify

VERIFY_HELP = """\
Two independent checks that a cleanup diff is comment-only; exits non-zero
when either objects.

Survivors: every changed line that is blank or starts with one of the file's
own comment tokens is dropped, and whatever remains is printed. A survivor is
either block-comment prose (a multi-line HTML comment body, a doc-comment
line that lost its leading `*`) or a real code change that must be undone —
read them, don't assume. Because a modified code line always survives, this
catches `retries = 3` becoming `retries = 5`, which nets to zero lines.

Line counts: if every non-blank changed line was a comment, the comment-line
delta equals the diff's net non-blank delta. The diff side never looks at a
comment token, so a wrong or missing LANGS entry makes the figures disagree —
the one failure the survivor check is blind to, being built on that table.

Blind spots, both noted in the output when they can apply: a JS generator
method (`*gen() {`) is filtered like doc-comment prose, and a changed file
whose extension has no LANGS entry is reported in full (the safe direction).
Known false alarm: removing a trailing comment shortens a line that stays in
the file, one mismatch per removal.
"""


def allowed_prefixes(ext):
    """Comment-token prefixes a changed line may start with, or None when
    the extension has no LANGS entry and nothing may be filtered."""
    toks = TOKENS.get(ext)
    if toks is None:
        return None
    line_toks, block_toks = toks
    prefixes = list(line_toks)
    for opener, closer in block_toks:
        prefixes += [opener, closer]
        if opener == "/*":
            prefixes.append("*")  # doc-comment body continuation
    return tuple(prefixes)


def comment_line_total(rev, exts, excludes, paths, seen):
    """Total comment lines at a revision, or in the working tree if rev is
    None. Records every file it counted in `seen`, so the diff side of the
    reconciliation can be held to the same scope.
    """
    total = 0
    for path in select_files(exts, excludes, paths, None, rev):
        seen.add(path)
        line_toks, block_toks = TOKENS[path.rsplit(".", 1)[-1]]
        try:
            lines = file_lines(path, rev)
        except (OSError, UnicodeDecodeError):
            continue
        for _line, body, _code, _branch in scan(lines, line_toks, block_toks,
                                                None):
            total += len(body)
    return total


def cmd_verify(args):
    base = resolve_commit(args.ref)
    exts, excludes = scope_of(args)

    survivors = defaultdict(list)
    files = set()
    filtered = blanks = 0
    star_filtered = False
    for path, sign, text in diff_body_lines(base, args.paths):
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        if any(skip in path for skip in excludes):
            continue
        # Without --ext, unknown extensions stay in and are reported in
        # full; with it, the caller asked for a narrower check on purpose.
        if args.ext and ext not in exts:
            continue
        files.add(path)
        stripped = text.strip()
        if not stripped:
            blanks += 1
            continue
        prefixes = allowed_prefixes(ext)
        if prefixes and stripped.startswith(prefixes):
            filtered += 1
            star_filtered |= "*" in prefixes and stripped.startswith("*")
            continue
        survivors[path].append(sign + text)

    print(f"comment-only check vs {args.ref}: {len(files)} changed file(s), "
          f"{filtered} comment line(s) and {blanks} blank line(s) filtered")
    if star_filtered:
        print("note: lines starting with `*` were filtered as doc-comment "
              "prose; a JS generator method has the same shape")
    if survivors:
        total = sum(len(lines) for lines in survivors.values())
        print(f"{total} surviving line(s) — block-comment prose is expected, "
              f"a code change must be undone:")
        for path in sorted(survivors):
            known = allowed_prefixes(path.rsplit(".", 1)[-1]
                                     if "." in path else "")
            note = "" if known else "  (no comment tokens known for this extension)"
            print(f"\n  {path}{note}")
            for line in survivors[path]:
                print(f"    {line[:170]}")
    else:
        print("OK: every changed line is a comment or blank")

    # The union of both sides: a file the cleanup added exists in one and not
    # the other, and its lines still belong in the comparison.
    scope = set()
    before = comment_line_total(base, exts, excludes, args.paths, scope)
    after = comment_line_total(None, exts, excludes, args.paths, scope)
    added = removed = 0
    for path, sign, text in diff_body_lines(base, args.paths):
        if path not in scope or not text.strip():
            continue
        if sign == "+":
            added += 1
        else:
            removed += 1

    comment_delta = after - before
    diff_delta = added - removed
    print()
    print(f"comment lines   {before} -> {after}   delta {comment_delta}")
    print(f"diff non-blank  +{added} -{removed}   delta {diff_delta}")
    if comment_delta == diff_delta:
        print("OK: every non-blank line the diff touched is accounted for as a comment")
    else:
        print(f"MISMATCH of {diff_delta - comment_delta} line(s): the diff changed")
        print("non-comment lines, or a trailing comment was removed. Read the diff.")
    return 1 if survivors or comment_delta != diff_delta else 0


# ---------------------------------------------------------------- compare

COMPARE_HELP = """\
Answers the question a cleanup review actually asks: what was deleted, what
was rewritten, and did any rewrite drop content the code cannot carry?

Blocks are matched by the code line beneath them, not by position. A
comment-only cleanup leaves that line untouched by definition, so it is a
stable key, and it separates the outcomes a text diff of comments runs
together: counting rewrites as deletions overstates what was lost; ignoring
them entirely hides the case where a rewrite quietly dropped a `@throws`, a
unit, or a worked example.
"""

# Content a signature cannot carry. A rewrite that drops one of these is worth a
# human look even when the block survives — see "The standard" in SKILL.md.
LOAD_BEARING = {
    "@example": re.compile(r"@example\b"),
    "@throws": re.compile(r"@throws\b|@exception\b"),
    "@deprecated": re.compile(r"@deprecated\b"),
    "@see": re.compile(r"@see\b"),
    "@default": re.compile(r"@default\b"),
    "url/reference": re.compile(r"https?://|ADR[- ]?\d|RFC\s?\d"),
    "ticket ref": re.compile(r"\b(TODO|FIXME|HACK|XXX)\b|#\d{2,}"),
    "unit/range": re.compile(r"\b\d+\s*(ms|s|km|m|h|hours?|days?|%|px)\b"),
    "null contract": re.compile(r"\bnull\b|\bundefined\b|\bNaN\b|\bempty\b", re.I),
}

SYNTAX = (("doc", "/**"), ("line", "//"), ("hash", "#"), ("markup", "<!--"),
          ("block", "/*"), ("dash", "--"))


def syntax_of(body):
    head = body[0].lstrip()
    if head.startswith("TRAILING:"):
        head = head[len("TRAILING:"):].lstrip()
    for name, tok in SYNTAX:
        if head.startswith(tok):
            return name
    return "other"


def anchor_of(body, code):
    """The code line a block sits on, with any trailing comment stripped.

    For a trailing comment the extractor reports the same line as both comment
    and code, so the comment has to come off or the anchor changes whenever the
    comment does — which would make every trailing edit look like a new anchor.
    """
    head = body[0]
    if head.startswith("TRAILING:"):
        text = head[len("TRAILING:"):].strip()
        cut = code.find(text)
        if cut != -1:
            return code[:cut].strip()
    return code.strip()


def is_comment_line(text, toks):
    line_toks, block_toks = toks
    stripped = text.strip()
    return (any(stripped.startswith(t) for t in line_toks)
            or any(stripped.startswith(b[0]) for b in block_toks)
            or stripped.startswith("*"))


def blocks_at(rev, exts, excludes, paths):
    """{(file, anchor): [body_lines, ...]} for one revision.

    Blocks sharing an anchor are kept as a list; pairing happens later by
    content, because an index would shift as soon as one of them is removed.
    """
    out = defaultdict(list)
    for path in select_files(exts, excludes, paths, None, rev):
        toks = TOKENS[path.rsplit(".", 1)[-1]]
        try:
            lines = file_lines(path, rev)
        except (OSError, UnicodeDecodeError):
            continue
        for line, body, code, _branch in scan(lines, toks[0], toks[1], None):
            anchor = anchor_of(body, code)
            # Find where the code actually starts. Two comment blocks separated
            # by a blank line make the first one's "next line" another comment,
            # so walk past comments to a real declaration.
            start = len(lines)
            for i in range(line - 1, len(lines)):
                if lines[i].strip() and not is_comment_line(lines[i], toks):
                    start = i
                    break
            if anchor and is_comment_line(anchor, toks) and start < len(lines):
                anchor = lines[start].strip()
            # Suppression context is the anchor's own code: from the anchor up
            # to the next comment, capped. It must not include the block (a
            # block would suppress its own markers) and must not run into the
            # next declaration's comment (whose text would suppress this one's).
            code_window = []
            for probe in lines[start:start + 10]:
                if is_comment_line(probe, toks) and probe.strip():
                    break
                code_window.append(probe)
            window = "\n".join(code_window)
            out[(path, anchor)].append((body, window))
    return out


def lost_content(before_body, after_body, code):
    """Load-bearing markers present before, gone after, and absent from the code.

    A marker the signature already carries was never the comment's contribution:
    `| undefined` in the return type makes "or undefined if unmapped" a
    restatement, and `networkTimeoutSeconds: 5` makes "5 s timeout" one. Only what
    the code cannot say counts as lost. `code` is a short window starting at the
    block, since a contract often sits inside the call, not on its first line.
    """
    b, a = " ".join(before_body), " ".join(after_body or [])
    lost = []
    for name, pat in LOAD_BEARING.items():
        hit = pat.search(b)
        if not hit or pat.search(a) or pat.search(code):
            continue
        # Code often spells a unit differently from prose — `5 s timeout` in the
        # comment, `networkTimeoutSeconds: 5` in the call. If every number in
        # the matched phrase is in the code, the value was never hidden.
        nums = re.findall(r"\d+", hit.group(0))
        if nums and all(re.search(rf"\b{n}\b", code) for n in nums):
            continue
        lost.append(name)
    return lost


def _tokens(body):
    return {w.lower() for w in re.findall(r"[A-Za-z0-9_]{3,}", " ".join(body))}


def pair_blocks(old, new):
    """Split into deleted / rewritten / added, pairing within each anchor group.

    One-to-one is the common case and pairs directly. Where an anchor repeats,
    pair by token overlap rather than by order, so removing one block does not
    make every later one look deleted-and-re-added.
    """
    deleted, rewritten, added = [], [], []
    for key in set(old) | set(new):
        olds, news = list(old.get(key, [])), list(new.get(key, []))
        if len(olds) == 1 and len(news) == 1:
            if olds[0][0] != news[0][0]:
                rewritten.append((key, olds[0][0], news[0][0], olds[0][1]))
            continue
        used = set()
        for ob, window in olds:
            ot = _tokens(ob)
            best, score = None, 0.0
            for i, (nb, _w) in enumerate(news):
                if i in used:
                    continue
                nt = _tokens(nb)
                overlap = len(ot & nt) / max(1, len(ot | nt))
                if overlap > score:
                    best, score = i, overlap
            if best is not None and score >= 0.1:
                used.add(best)
                if news[best][0] != ob:
                    rewritten.append((key, ob, news[best][0], window))
            else:
                deleted.append((key, ob, window))
        for i, (nb, _w) in enumerate(news):
            if i not in used:
                added.append((key, nb))
    return deleted, rewritten, added


def cmd_compare(args):
    base = resolve_commit(args.base)
    after = resolve_commit(args.after) if args.after else None
    exts, excludes = scope_of(args)

    old = blocks_at(base, exts, excludes, args.paths)
    new = blocks_at(after, exts, excludes, args.paths)
    deleted, rewritten, added = pair_blocks(old, new)

    def totals(groups):
        blocks = 0
        lines = defaultdict(int)
        for bodies in groups.values():
            for body, _window in bodies:
                blocks += 1
                lines[syntax_of(body)] += len(body)
        return blocks, lines
    nb_old, lo = totals(old)
    nb_new, ln = totals(new)

    print(f"comparing {args.base} -> {args.after or 'working tree'}")
    print(f"  blocks   {nb_old} -> {nb_new}")
    print(f"  deleted {len(deleted)}   rewritten {len(rewritten)}   added {len(added)}")
    print()
    print(f"  {'syntax':10} {'lines before':>13} {'lines after':>12} {'delta':>7}"
          f" {'deleted':>8} {'rewritten':>10}")
    dels = Counter(syntax_of(b) for _k, b, _w in deleted)
    rews = Counter(syntax_of(b) for _k, b, _a, _w in rewritten)
    for syn in sorted(set(lo) | set(ln)):
        print(f"  {syn:10} {lo[syn]:>13} {ln[syn]:>12} {ln[syn]-lo[syn]:>+7}"
              f" {dels[syn]:>8} {rews[syn]:>10}")

    flagged = []
    for (path, anchor), body, window in deleted:
        lost = lost_content(body, None, window)
        if lost:
            flagged.append((path, anchor, syntax_of(body), lost, body, None))
    for (path, anchor), body, after_body, window in rewritten:
        lost = lost_content(body, after_body, window)
        if lost:
            flagged.append((path, anchor, syntax_of(body), lost, body, after_body))

    print()
    if flagged:
        print(f"  {len(flagged)} block(s) lost content the code does not carry — review each:")
        for path, anchor, syn, lost, body, after_body in flagged:
            verb = "deleted" if after_body is None else "rewritten"
            print(f"\n    {path}  [{syn}, {verb}, lost: {', '.join(lost)}]")
            print(f"      anchor: {anchor[:88]}")
            print(f"      before: {' '.join(body)[:170]}")
            if after_body is not None:
                print(f"      after:  {' '.join(after_body)[:170]}")
    else:
        print("  no block lost content the code does not carry")

    if args.detail:
        print("\n  === deleted ===")
        for (path, _a), body, _w in deleted:
            print(f"    {path} [{syntax_of(body)}] {' '.join(body)[:110]}")
        print("\n  === rewritten ===")
        for (path, _a), body, after_body, _w in rewritten:
            print(f"    {path} [{syntax_of(body)}] {' '.join(body)[:100]}")
            print(f"      -> {' '.join(after_body)[:100]}")

    return 1 if flagged else 0


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    p = sub.add_parser(
        "extract", help="comment blocks with their following code line",
        description=EXTRACT_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    add_scope_args(p)
    p.add_argument("--since", metavar="REF",
                   help="only files changed since the merge base of REF and "
                        "HEAD, including uncommitted work")
    p.add_argument("--rev", metavar="REV",
                   help="read files as of REV instead of the working tree; "
                        "reads through git, never checks anything out")
    p.add_argument("--tsv", action="store_true",
                   help="emit file/line/branch/comment/code columns instead of "
                        "the readable listing, to build the report table from")
    p.add_argument("--count", action="store_true",
                   help="print totals only, to size the scope up front")
    p.set_defaults(func=cmd_extract, parser=p)

    p = sub.add_parser(
        "verify", help="check that a cleanup diff is comment-only, two ways",
        description=VERIFY_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ref", nargs="?", default="HEAD", metavar="REF",
                   help="commit to compare against (default: HEAD)")
    add_scope_args(p)
    p.set_defaults(func=cmd_verify, parser=p)

    p = sub.add_parser(
        "compare", help="the comments at two revisions, block by block",
        description=COMPARE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("base", metavar="BASE", help="revision to compare from")
    add_scope_args(p)
    p.add_argument("--to", metavar="REF", dest="after",
                   help="revision to compare to (default: the working tree)")
    p.add_argument("--detail", action="store_true",
                   help="list every deleted and rewritten block")
    p.set_defaults(func=cmd_compare, parser=p)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
