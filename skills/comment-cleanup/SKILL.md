---
name: comment-cleanup
description: Remove comments that say nothing the code does not already say, and reword the survivors to be as concise as possible. Use when the user asks to "clean up comments", "remove useless comments", "trim the comments", "there are too many comments", or wants a comment pass over files, a directory, or a branch.
disable-model-invocation: true
---

# Comment Cleanup

Make every comment earn its place. Delete the ones that only repeat the code. Reword
the rest so they carry their content in the fewest words. Change comment text only —
never code, identifiers, strings, or behaviour.

## Scope

- The user names paths → those paths.
- "This branch" or "this PR" → files changed since the base branch
  (`git diff --name-only "$(git merge-base main HEAD)"` plus uncommitted work).
- No qualifier → ask, or default to the files changed in the working tree. Do not
  sweep the whole repository unless the user asks for that.

## Decide per comment: delete, reword, or keep

**Delete** when the code already says it:

- Restates the line below it (`// increment the counter` over `counter++`).
- Labels a section the reader can see (`<!-- Header -->`, `// --- Helpers ---`).
  Banner dashes are decoration and always go; keep the label only if it carries
  information on its own.
- Records history that git holds ("was 52", "previously returned X", "moved to").
- A doc comment that restates the signature (`@param options - Configuration
  options`, `/** Returns the user's name. */` over `getName(): string`).

**Reword** when there is real content wrapped in padding. Keep the reason, the
constraint, the unit, the reference — drop the narration around it. Three lines of
story usually compress to one line that still says why.

**Keep verbatim** when the comment says something the code cannot:

- Why, not what: invariants, ordering constraints, protocol or API quirks, a
  non-obvious approach a reader would try to "fix", a rejected alternative.
- Usage a signature cannot carry: `@example`, `@throws`, "call after mount".
- When unsure, keep. A kept comment costs little; a deleted reason is gone.

**Never touch:**

- Licence and copyright headers, generated-file markers.
- Tooling directives: `eslint-disable`, `@ts-expect-error`, `# noqa`,
  `# type: ignore`, `//go:build`, `#[allow(...)]`, and the like.
- `TODO` / `FIXME` / `HACK` and anything referencing a ticket or issue.
- Commented-out code — flag it for the user, do not delete it.

Flag stale comments that describe code which no longer exists — the code may have
been removed by accident, which is a bug, not a comment problem.

## Rewording

Apply the **simple-english** skill (pragmatic mode) to the comments you keep. The
rules that matter most here:

- Short sentences: 25 words maximum, one topic per sentence.
- Active voice, simple tenses. "The cache expires after 60 s", not "the cache will
  have been expired".
- One name per thing — use the identifier the code uses, not a synonym.
- A verb, not a noun chain: "retries the request", not "performs request retry
  logic".

Match the voice and comment style of the surrounding file. A rewrite that reads as
freshly generated is a bad rewrite.

## Apply and verify

1. Edit the comments, grouped by directory or module.
2. Run the formatter if the project has one — it may reflow what you rewrote.
3. Check the diff is comment-only: every changed line in `git diff` must be blank or
   comment text. Undo any line that is not.
4. Run the project's lint and tests if they are quick to run.
5. Summarise: how many comments deleted, reworded, kept, and anything flagged
   (commented-out code, stale comments).

Commit only when the user asks; keep the cleanup in its own commit, separate from
feature work.
