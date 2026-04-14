# claude-code-tools

Companion utilities for people who use [Claude Code](https://claude.com/claude-code) seriously. A monorepo — each tool lives in its own subdirectory with its own `pyproject.toml`, its own README, and its own release cadence. No shared runtime. Shared home.

## Tools

| Tool | What it does | Status |
|---|---|---|
| [`gitwat/`](./gitwat) | git, wat? — a deterministic git coach that maps plain-language intents to git commands, always shows the command before running it, and filters suggestions by repo state. No LLM. | v0.1.0 |

## Design principles

- **Deterministic first.** If a tool can be built without AI, it is. AI is added only when ambiguity is genuinely the problem being solved.
- **Teach, don't automate.** Tools that touch the user's environment always show the underlying command. The goal is building fluency, not creating dependency.
- **State-aware over clever.** Real-time context (repo state, file system, config) beats language understanding for most practical tasks.
- **One tool, one job.** Each subdirectory solves one problem well. Cross-tool abstractions only emerge when real duplication appears.

## Install a tool

Each tool is independently installable:

```bash
pip install git+https://github.com/holbizmetrics/claude-code-tools.git#subdirectory=gitwat
```

Or clone and install editable for development:

```bash
git clone https://github.com/holbizmetrics/claude-code-tools.git
cd claude-code-tools/gitwat
pip install -e .
```

## License

MIT
