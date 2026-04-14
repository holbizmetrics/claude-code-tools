# gitwat

**git, wat?** — A deterministic git coach. Tells you the exact command for what you're trying to do, always shows it before offering to run it, and filters suggestions by real-time repo state. No LLM, no network, no ambiguity.

## What it is

A Python CLI that maps plain-language intents to git commands using a hand-curated pain-point database and fuzzy matching. Every suggestion is:

- **The command you could have typed yourself** — learning tool first, automation tool second.
- **Deterministic** — same input, same output, forever. No model drift, no hallucinated flags.
- **State-aware** — a suggestion that doesn't apply to your current repo state is filtered out before you see it. `git stash pop` doesn't appear if you have no stashes.
- **Safety-tiered** — read-only runs silently, worktree/history changes ask for confirmation, destructive actions require a typed confirmation.

## Install

```bash
pip install -e .
```

Requires Python 3.11+ (for `tomllib`) and `rapidfuzz`.

## Usage

```bash
gitwat show remotes
gitwat what branch am i on
gitwat "undo my last commit"
gitwat --run what changed
```

Pass `--run` to offer execution after displaying the command. Without it, `gitwat` only shows the command and explanation.

## Adding pain points

Edit `src/gitwat/painpoints.toml`. Each entry:

```toml
[[painpoint]]
id = "category.name"
intents = ["canonical phrasing", "another way to say it", "..."]
command = "git ..."
explanation = "One sentence."
safety = "readonly"  # or worktree | history | destructive
requires = ["in-repo", "has-commits"]  # optional
warning = "Optional caveat shown before execution."
```

State checks available in `requires`:

| Check | Passes when |
|---|---|
| `in-repo` | inside a git working tree |
| `has-commits` | repo has at least one commit |
| `has-upstream` | current branch has an upstream tracking branch |
| `dirty` | working tree has uncommitted changes |
| `detached-head` | HEAD is detached |
| `has-stash` | stash list is non-empty |
| `has-remote` | at least one remote is configured |

## Design

- **Why not AI?** Because the problem isn't "what did the user mean" (you know what you mean). It's "what's the exact syntax." That's a dictionary problem, not a translation problem. AI adds latency, cost, nondeterminism, and a hallucination surface for zero benefit.
- **Why fuzzy match?** So you don't have to enumerate every phrasing in `intents`. Write 3-4 canonical phrases; `rapidfuzz` handles typos and word-order variation.
- **Why show all candidates when scores are close?** Because the teaching moment is often realizing that one English phrase legitimately maps to several commands, and picking between them is where the learning happens.

## Status

v0.1.0 — seed database with ~12 pain points covering remotes, status, log, branch, diff, undo, stash, and push. Expand as real pain points appear in actual use.
