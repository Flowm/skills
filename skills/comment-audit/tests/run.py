#!/usr/bin/env python3
"""Every comment-audit suite in one file. Exits non-zero if any check fails.

    skills/comment-audit/tests/run.py

Each suite builds its own scratch repository and exercises one subcommand of
scripts/comment_audit.py through its CLI, the way the skill itself runs it.
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "scripts", "comment_audit.py")

_passed = 0
_failed = 0


def section(title):
    print(f"-- {title} --")


def check(label, expected, actual):
    global _passed, _failed
    if expected == actual:
        _passed += 1
        print(f"  PASS  {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")
        print(f"        expected [{expected!r}], got [{actual!r}]")


def run(*args):
    """The tool, run in the scratch repo."""
    return subprocess.run([sys.executable, TOOL, *args],
                          capture_output=True, text=True)


def git(*args):
    done = subprocess.run(("git",) + args, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"test setup: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def worktrees():
    return len(git("worktree", "list").strip().splitlines())


def new_repo():
    """A scratch git repository, removed when the tests exit."""
    work = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, work, ignore_errors=True)
    os.chdir(work)
    git("init", "-qb", "main", ".")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Comment Audit Tests")


def write(path, content):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def append(path, content):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(content)


def commit_all(message="checkpoint"):
    git("add", "-A")
    git("commit", "-qm", message)


def checkout():
    git("checkout", "-q", ".")


def edit(path, old, new):
    """Replace old with new in path; the fixture must contain it."""
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    if old not in content:
        sys.exit(f"test setup: {old!r} not found in {path}")
    write(path, content.replace(old, new))


def drop_lines(path, needle):
    """Delete every line containing the literal needle."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.readlines()
    kept = [line for line in lines if needle not in line]
    if len(kept) == len(lines):
        sys.exit(f"test setup: no line of {path} contains {needle!r}")
    write(path, "".join(kept))


def lines_with(text, needle):
    """Number of lines containing the literal needle (grep -cF)."""
    return sum(needle in line for line in text.splitlines())


def lines_matching(text, pattern):
    """Number of lines matching the regex (grep -cE)."""
    return sum(bool(re.search(pattern, line)) for line in text.splitlines())


# ---------------------------------------------------------------- extract

def suite_extractor():
    """Extraction correctness: which lines count as comments, and the modes."""
    new_repo()
    write("a.css", """/* Palette shared with the theme switcher */
#app {
  --main-color: red;
}
""")
    write("b.rs", """// Real comment
#[derive(Debug, Clone)]
pub struct Foo {}
""")
    write("c.js", """/*
   Block body with no leading star
   second line
*/
const x = 1;
""")
    write("d.ts", """const a = 1; // see https://github.com/foo/bar/issues/42 for why
class Clock {
  #offset = 0;
}
""")
    write("e.py", """# Real python comment
x = 1  # trailing python comment
""")
    commit_all()

    def counted(field, *args):
        out = run("extract", "--count", *args).stdout
        for line in out.splitlines():
            if line.startswith(field + "="):
                return int(line.split("=", 1)[1])
        return None

    section("'#' is code outside the Python family")
    out = run("extract").stdout
    check("no CSS id selector as comment text", 0, lines_with(out, "| #app"))
    check("no Rust attribute as comment text", 0, lines_with(out, "| #[derive"))
    check("no TS private field as comment text", 0, lines_with(out, "| #offset"))
    check("CSS selector is reported as the code line", 1, lines_with(out, "> #app {"))
    check("Python '#' still opens a comment", 1,
          lines_with(out, "| # Real python comment"))

    section("block comments run to their closing delimiter")
    check("body line without a leading star is captured", 1,
          lines_with(out, "| Block body with no leading star"))
    check("closing delimiter is captured", 1,
          sum(line == "    | */" for line in out.splitlines()))
    check("the line after the block is the code line", 1,
          lines_with(out, "> const x = 1;"))

    section("trailing comments")
    check("a URL in a trailing comment does not drop it", 1,
          lines_with(out, "TRAILING: // see https://github.com"))
    check("trailing Python comment found", 1,
          lines_with(out, "TRAILING: # trailing python comment"))

    section("output modes")
    blocks_in_text = lines_matching(out, r"^  L")
    check("--count blocks matches the text listing", blocks_in_text, counted("blocks"))
    check("--count comment_lines matches the text listing",
          lines_matching(out, r"^    \| "), counted("comment_lines"))
    tsv_rows = [line for line in run("extract", "--tsv").stdout.splitlines() if line]
    check("--tsv emits one row per block", blocks_in_text, len(tsv_rows))
    check("--tsv rows have five columns", {5},
          {len(row.split("\t")) for row in tsv_rows})
    check("--tsv and --count refuse to combine", 2,
          run("extract", "--tsv", "--count").returncode)

    section("--rev reads a commit without checking anything out")
    write("g.ts", "// Added after the baseline.\nexport const later = 1;\n")
    baseline = git("rev-parse", "HEAD").strip()
    commit_all("add g.ts")
    check("--rev sees the old state", 0,
          lines_with(run("extract", "--rev", baseline).stdout,
                     "Added after the baseline"))
    check("the working tree still sees the new state", 1,
          lines_with(run("extract").stdout, "Added after the baseline"))
    check("--rev output matches the working tree at the same commit",
          counted("comment_lines"), counted("comment_lines", "--rev", "HEAD"))
    check("--rev combines with --tsv", 1,
          lines_with(run("extract", "--rev", "HEAD", "--tsv").stdout,
                     "Added after the baseline"))
    check("--rev rejects an unknown commit", 1,
          run("extract", "--rev", "nope").returncode)
    check("--rev and --since refuse to combine", 2,
          run("extract", "--rev", "HEAD", "--since", "main").returncode)
    check("--rev creates no worktree", 1, worktrees())
    check("--rev leaves the tree clean", "", git("status", "--porcelain").strip())

    section("scope and error paths")
    check("--ext filters", ["==== e.py"],
          [line for line in run("extract", "--ext", "py").stdout.splitlines()
           if line.startswith("====")])
    proc = run("extract", "--ext", "bogus")
    check("unknown --ext exits 1", 1, proc.returncode)
    check("unknown --ext names the flag's known set", 1,
          lines_with(proc.stderr, "known:"))
    proc = run("extract", "--ext", "py", "--exclude", "e.py")
    check("--exclude drops a path", "", proc.stdout)
    check("no match exits 1", 1, proc.returncode)


# ------------------------------------------------- verify: survivor check

def suite_verify_survivors():
    """The Phase 2 safety net: silent for comment-only edits, and it must
    never hide a code change."""
    new_repo()

    def verify(label, expect, *args):
        got = "silent" if run("verify", *args).returncode == 0 else "reported"
        check(label, expect, got)
        checkout()

    write("a.ts", """class Clock {
  // Offset applied before formatting.
  #offset = 0;
  #calibrated = false;
}
""")
    write("b.css", """/* Palette shared with the theme switcher */
#app {
  --main-color: red;
}
""")
    write("c.vue", """<template>
  <!-- Legend -->
  <div id="root">x</div>
</template>
""")
    write("d.js", """/*
   Block body with no leading star
*/
const x = 1;
""")
    write("e.py", """# Offset applied before formatting.
OFFSET = 0
""")
    commit_all()

    section("comment-only edits stay silent")
    edit("a.ts", "// Offset applied before formatting.", "// Offset in seconds.")
    verify("TS line comment reworded", "silent")
    edit("b.css", "/* Palette shared with the theme switcher */", "/* Palette. */")
    verify("CSS block comment reworded", "silent")
    drop_lines("c.vue", "<!-- Legend -->")
    verify("Vue HTML comment deleted", "silent")
    edit("e.py", "# Offset applied before formatting.", "# Offset in seconds.")
    verify("PY comment reworded, no extra flag needed", "silent")

    section("code changes are always reported")
    edit("a.ts", "#offset = 0;", "#offset = 3600;")
    verify("TS private field value changed", "reported")
    drop_lines("a.ts", "#calibrated")
    verify("TS private field deleted", "reported")
    edit("b.css", "#app {", "#main {")
    verify("CSS id selector renamed", "reported")
    edit("b.css", "--main-color: red;", "--main-color: blue;")
    verify("CSS custom property changed", "reported")
    edit("c.vue", '<div id="root">x</div>', '<div id="root">y</div>')
    verify("Vue markup changed", "reported")
    edit("e.py", "OFFSET = 0", "OFFSET = 3600")
    verify("PY code changed", "reported")
    edit("a.ts", "// Offset applied before formatting.", "// Offset.")
    edit("a.ts", "#offset = 0;", "#offset = 42;")
    verify("comment and code in one hunk", "reported")
    edit("d.js", "Block body with no leading star", "Block body, reworded")
    verify("block-comment body (safe false positive)", "reported")

    section("unknown extensions are reported in full")
    write("conf.json", '{ "retries": 3 }\n')
    commit_all("add conf.json")
    edit("conf.json", '"retries": 3', '"retries": 5')
    proc = run("verify")
    check("JSON change survives", 1, proc.returncode)
    check("  and the file is flagged as unknown", 1,
          lines_with(proc.stdout, "no comment tokens known"))
    checkout()

    section("blank lines are filtered but counted")
    write("f.ts", "// Standalone comment.\n\nconst y = 2;\n")
    commit_all("add f.ts")
    write("f.ts", "const y = 2;\n")
    proc = run("verify")
    check("comment plus its blank line is silent", 0, proc.returncode)
    check("  and the blank line shows up in the tally", 1,
          lines_with(proc.stdout, "1 blank line(s) filtered"))
    checkout()

    section("the generator-method blind spot is announced")
    write("g.js", "class G {\n  * gen() { yield 1; }\n}\n")
    commit_all("add g.js")
    edit("g.js", "yield 1", "yield 2")
    proc = run("verify")
    check("a filtered star line prints the caveat", 1,
          lines_with(proc.stdout, "generator method"))
    checkout()

    section("scope and error paths")
    edit("a.ts", "#offset = 0;", "#offset = 99;")
    verify("a path argument narrows the check", "silent", "HEAD", "e.py")
    edit("a.ts", "#offset = 0;", "#offset = 99;")
    verify("the same change is reported unscoped", "reported")
    check("an unknown ref exits 1", 1, run("verify", "nope").returncode)

    section("read-only")
    write("dirty.ts", "// Uncommitted edit.\nexport const dirty = 1;\n")
    run("verify")
    check("never creates a worktree", 1, worktrees())
    check("leaves the uncommitted file in place", True, os.path.exists("dirty.ts"))
    os.remove("dirty.ts")


# --------------------------------------------- verify: line-count check

def suite_verify_counts():
    """The line-count cross-check inside verify: comment-line delta against
    the diff's non-blank delta, including the known false alarm."""
    new_repo()
    write("a.ts", """// Offset applied before formatting. Restates the field below, which is
// exactly the sort of thing this audit removes.
export const offset = 0;

// Kept: the upstream API caps retries at three, so raising this does nothing.
export const retries = 3;
""")
    write("b.py", "# Restated comment.\nVALUE = 1\n")
    commit_all("init")

    section("a comment-only cleanup reconciles")
    write("a.ts", """export const offset = 0;

// The upstream API caps retries at three, so raising this does nothing.
export const retries = 3;
""")
    proc = run("verify", "HEAD")
    check("exits 0", 0, proc.returncode)
    check("reports the deltas as equal", 1,
          lines_with(proc.stdout, "accounted for as a comment"))
    check("  comment lines fell by two", 1,
          lines_matching(proc.stdout, r"^comment lines .* delta -2$"))
    checkout()

    section("an added line of code is caught")
    append("a.ts", "\nexport const SNEAKY = 1;\n")
    proc = run("verify", "HEAD")
    check("exits 1", 1, proc.returncode)
    check("reports a mismatch", 1, lines_matching(proc.stdout, r"^MISMATCH"))
    checkout()

    section("a deleted line of code is caught")
    drop_lines("a.ts", "export const retries")
    proc = run("verify", "HEAD")
    check("exits 1", 1, proc.returncode)
    check("reports a mismatch", 1, lines_matching(proc.stdout, r"^MISMATCH"))
    checkout()

    section("a MODIFIED code line reconciles to zero but still fails verify")
    # One deletion plus one insertion cancels, so the counts agree — the
    # survivor check is what catches it. This is why verify runs both.
    write("a.ts", """export const offset = 0;

// The upstream API caps retries at three.
export const retries = 5;
""")
    proc = run("verify", "HEAD")
    check("the counts alone would have passed", 1,
          lines_with(proc.stdout, "accounted for as a comment"))
    check("  but the surviving line fails the run", 1, proc.returncode)
    check("  and it is printed for review", 1,
          lines_with(proc.stdout, "export const retries = 5;"))
    checkout()

    section("scope applies to the diff too, not just the comment count")
    # A code change outside the scope must not make a scoped run disagree.
    write("b.py", "# Comment reworded.\nVALUE = 1\n")
    append("a.ts", "\nexport const OUT_OF_SCOPE = 1;\n")
    check("scoped to py, the ts code change is ignored", 0,
          run("verify", "HEAD", "--ext", "py").returncode)
    check("unscoped, the same change is caught", 1,
          run("verify", "HEAD").returncode)
    check("scoping by path behaves the same", 0,
          run("verify", "HEAD", "b.py").returncode)
    checkout()

    section("a removed line of content that begins with --- is still counted")
    # `---` is a document separator in YAML, not a comment. Its diff line reads
    # `----`, which a prefix test mistakes for a file header.
    write("doc.yaml", "---\n# A comment.\nkey: value\n")
    commit_all("add doc.yaml")
    drop_lines("doc.yaml", "---")
    check("deleting it is reported as a code change", 1,
          run("verify", "HEAD").returncode)
    checkout()

    section("the documented false alarm")
    # Removing a trailing comment shortens a line that stays in the file: the
    # diff's non-blank net is zero while the comment count drops by one.
    write("c.ts", "export const x = 1; // restates the name\n")
    commit_all("add a trailing comment")
    write("c.ts", "export const x = 1;\n")
    proc = run("verify", "HEAD")
    check("trailing-comment removal reports a mismatch", 1, proc.returncode)
    check("  and the mismatch is exactly one line", 1,
          lines_with(proc.stdout, "MISMATCH of 1 line"))
    checkout()

    section("read-only")
    # The base state is read with `git show`, so nothing is checked out and
    # uncommitted work is safe. Prove it with dirty state in the tree.
    write("dirty.ts", "// Uncommitted edit.\nexport const dirty = 1;\n")
    run("verify", "HEAD")
    check("never creates a worktree", 1, worktrees())
    check("leaves the uncommitted file in place", True, os.path.exists("dirty.ts"))
    check("does not move HEAD", "main",
          git("rev-parse", "--abbrev-ref", "HEAD").strip())
    os.remove("dirty.ts")


# ---------------------------------------------------------------- scoping

def suite_scoping():
    """Path and branch scoping, including what --since can and cannot see."""
    new_repo()
    write("src/api/client.ts",
          '// Pre-existing comment in api.\nexport const base = "/v1";\n')
    write("src/ui/panel.ts",
          "// Pre-existing comment in ui.\nexport const width = 100;\n")
    write("README.md", "<!-- Pre-existing markdown comment -->\n# Title\n")
    commit_all("init")

    # main moves on after the branch forks; those commits must stay out of scope.
    git("checkout", "-qb", "feature")
    git("checkout", "-q", "main")
    write("src/api/legacy.ts",
          "// Comment added on main after the fork.\nexport const old = true;\n")
    commit_all("main advances")
    git("checkout", "-q", "feature")
    write("src/api/client.ts", """// Pre-existing comment in api.
export const base = "/v1";

// Added by the branch: retries are capped upstream.
export function get(path: string) {}
""")
    commit_all("branch work")
    append("src/ui/panel.ts",
           "\n// Uncommitted comment in ui.\nexport const height = 50;\n")

    def file_headers(*args):
        out = run("extract", *args).stdout
        return [line for line in out.splitlines() if line.startswith("====")]

    section("path scoping")
    check("no arguments covers the whole repo", 3, len(file_headers()))
    check("a directory scopes to its files",
          ["==== src/ui/panel.ts"], file_headers("src/ui"))
    check("several pathspecs combine", 2, len(file_headers("src/api", "README.md")))
    check("paths combine with --ext", 1,
          lines_with(run("extract", "src", "--ext", "ts").stdout,
                     "Pre-existing comment in api"))

    section("branch scoping")
    out = run("extract", "--since", "main").stdout
    listing = out.splitlines()
    check("excludes files main added after the fork", 0, lines_with(out, "legacy.ts"))
    check("includes committed branch work", 1, lines_with(out, "Added by the branch"))
    check("includes uncommitted work", 1, lines_with(out, "Uncommitted comment in ui"))
    check("marks blocks the branch touched", 2,
          lines_matching(out, r"^  L.*\[branch\]"))
    before = listing[next(i for i, line in enumerate(listing)
                          if "Pre-existing comment in api" in line) - 1]
    check("leaves untouched blocks in the same file unmarked", False,
          "[branch]" in before)
    check("the trailer does not use the '====' file prefix", 2,
          sum(line.startswith("====") for line in listing))
    check("the trailer counts files and marks", 1,
          lines_with(out, "---- scope: 2 file(s) changed since main, 2 comment"))
    check("the trailer has no literal '[branch]' to miscount", 0,
          sum("[branch]" in line for line in listing if line.startswith("----")))
    check("--since combines with a pathspec", 1,
          len(file_headers("--since", "main", "src/api")))

    section("limits of --since")
    # A comment the branch deleted is gone from the working tree, so nothing
    # can mark it. Auditing someone else's cleanup needs `compare` instead.
    commit_all("keep the uncommitted work")
    git("rm", "-q", "src/ui/panel.ts")
    commit_all("branch deletes a file")
    out = run("extract", "--since", "main").stdout
    check("a deleted file is not listed", 0, lines_with(out, "panel.ts"))
    check("its comments are unreachable from the working tree", 0,
          lines_with(out, "Pre-existing comment in ui"))

    section("error paths")
    proc = run("extract", "--since", "nope")
    check("an unknown ref exits 1", 1, proc.returncode)
    check("an unknown ref says so", 1, lines_with(proc.stderr, "merge base"))
    check("an empty scope exits 1", 1,
          run("extract", "--since", "main", "--ext", "md").returncode)


# ---------------------------------------------------------------- compare

def suite_compare():
    """Block-level revision comparison: deleted vs rewritten, loss detection."""
    new_repo()
    write("api.ts", """/**
 * Register the service worker and expose its update state.
 *
 * @param options - Configuration options
 * @example
 * const { needRefresh, updateApp } = usePWAUpdate();
 * watch(needRefresh, (v) => { if (v) updateApp(); });
 */
export function usePWAUpdate(options: Options = {}) {}

/** Map a non-empty set back to its view id. */
export function viewOf(active: Set<Id>): ViewId {}

/** The family of a model, or `undefined` if unmapped. */
export function familyOf(id: string): string | undefined {}

// Geocoding: network-first with a 5 s timeout.
registerRoute(
  /geocoding/,
  new NetworkFirst({ networkTimeoutSeconds: 5 }),
);

// Restates the line below.
export const total = 1;
""")
    commit_all("init")

    def rewrite_doc_block(replacement):
        with open("api.ts", encoding="utf-8") as handle:
            content = handle.read()
        start = content.index("/**")
        end = content.index(" */\nexport function usePWAUpdate")
        write("api.ts", content[:start] + replacement + content[end:])

    section("deleted and rewritten are told apart")
    rewrite_doc_block("/**\n * Service worker registration and update state.\n")
    edit("api.ts", "// Restates the line below.\n", "")
    proc = run("compare", "HEAD")
    check("the rewrite counts as rewritten, not deleted", 1,
          lines_with(proc.stdout, "deleted 1   rewritten 1"))
    check("reports per-syntax line deltas", 1, lines_matching(proc.stdout, r"^  doc "))

    section("losing a worked example is flagged")
    check("flags the dropped @example", 1, lines_with(proc.stdout, "lost: @example"))
    check("exits non-zero when something was flagged", 1, proc.returncode)
    checkout()

    section("a marker the code already carries is not a loss")
    # `| undefined` is in the signature, and networkTimeoutSeconds: 5 is in the
    # call, so neither comment was carrying anything the reader couldn't see.
    drop_lines("api.ts", "`undefined` if unmapped")
    drop_lines("api.ts", "5 s timeout")
    proc = run("compare", "HEAD")
    check("signature carries the null contract", 0,
          lines_with(proc.stdout, "null contract"))
    check("the call below carries the timeout", 0,
          lines_with(proc.stdout, "unit/range"))
    check("exits zero when nothing was flagged", 0, proc.returncode)
    checkout()

    section("a precondition the type cannot express is a loss")
    drop_lines("api.ts", "Map a non-empty set")
    check("flags the dropped non-empty precondition", 1,
          lines_with(run("compare", "HEAD").stdout, "lost: null contract"))
    checkout()

    section("the suppression window never includes the comment itself")
    # Regression: a window starting at the block let a block suppress its own
    # markers, so every loss came back clean.
    rewrite_doc_block("/**\n * Registration.\n")
    check("still flags @example when the block held it", 1,
          lines_with(run("compare", "HEAD").stdout, "lost: @example"))
    checkout()

    section("repeated anchors pair by content, not by order")
    write("routes.ts", """// Alpha route: cached for a day.
registerRoute(alpha);
// Beta route: network only.
registerRoute(beta);
// Gamma route: stale-while-revalidate.
registerRoute(gamma);
""")
    commit_all("add routes")
    drop_lines("routes.ts", "Alpha route")
    check("removing the first of three reports one deletion", 1,
          lines_with(run("compare", "HEAD", "routes.ts").stdout,
                     "deleted 1   rewritten 0   added 0"))
    checkout()

    section("scope and error paths")
    check("--ext filters", 0, run("compare", "HEAD", "--ext", "py").returncode)
    proc = run("compare", "nope")
    check("an unknown base exits 1", 1, proc.returncode)
    check("an unknown base says so", 1, lines_with(proc.stderr, "not a commit"))
    check("creates no worktree", 1, worktrees())


def main():
    for suite in (suite_extractor, suite_verify_survivors, suite_verify_counts,
                  suite_scoping, suite_compare):
        print(f"\n=== {suite.__name__}")
        suite()
    print(f"\n{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
