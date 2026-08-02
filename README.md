# skills

Personal [agent skills](https://skills.sh) for Claude Code, Cursor, Zed, and other coding agents.

## Skills

| Skill | Description |
| ----- | ----------- |
| [git-commit](skills/git-commit/SKILL.md) | Git commits following Conventional Commits, attributed with an `Assisted-by:` trailer |
| [frontend-project-structure](skills/frontend-project-structure/SKILL.md) | Default structure, tooling, and library choices for frontend web apps (Vue 3 + Vite + TypeScript + Tailwind on Cloudflare Workers) |
| [comment-audit](skills/comment-audit/SKILL.md) | Triage a codebase's comments for value and verbosity, then rewrite or remove the ones that don't earn their place |

## Install

```bash
npx skills add Flowm/skills
```

## Development

Skills that ship executable scripts carry their own tests. Run them after changing a
script or the commands quoted in its `SKILL.md`:

```bash
skills/comment-audit/tests/run.sh
```

## License

[MIT](LICENSE)
