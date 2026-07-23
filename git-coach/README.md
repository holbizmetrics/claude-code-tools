# git-coach

A deterministic git coach. Tells you the exact command for what you're trying to do, always shows it before offering to run it, and filters suggestions by real-time repo state. No LLM, no network, no ambiguity.

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
git-coach show remotes
git-coach what branch am i on
git-coach "undo my last commit"
git-coach --run what changed
```

Pass `--run` to offer execution after displaying the command. Without it, `git-coach` only shows the command and explanation.

### Interactive coach mode

Run `git-coach` with **no query** to drop into an interactive session — a welcome banner, then a `git-coach>` prompt where you ask in plain words and get the command back, question after question:

```bash
git-coach              # interactive, display-only
git-coach --run        # interactive, and offer to run each command
```

Type `help` to re-show the banner, `quit` / `exit` to leave. Good for beginners exploring "how do I…" without re-typing `git-coach` each time.

You can also type a **real git command directly** (e.g. `git ls-files`) — the coach recognizes it instead of fuzzy-guessing — and prefix any command with **`run `** (e.g. `run git ls-files`) to execute it on the spot. Commands that can rewrite or lose work (`--force`, `reset --hard`, `clean -f`, `rebase`, …) ask for confirmation first.

## Finding lost & duplicate work: `locate` and `dedup`

Two subcommands answer a different question from the rest of the tool — not "what's the git command?" but **"is this folder already in one of my repos, and is it redundant, ahead, or an orphan?"** They need no repo in the current directory and use no fuzzy matching: a file's identity is its **normalized content hash** (CRLF/CR flattened to LF, then SHA-256), so provenance is a set operation, not a search. Git already content-addresses every file it tracks — these commands just ask across a whole collection of repos at once.

```bash
# Which repo does this detached folder belong to, and what is its relationship?
git-coach locate ./some-folder --repos ~/code
#   REDUNDANT  every file is already in your repos -> safe to delete the folder
#   AHEAD      the folder holds files no repo has  -> stranded work, rescue it
#   ORPHAN     none of it is in any scanned repo   -> it needs a home

# Which files under here are duplicates, or same-named forks?
git-coach dedup ./messy-dir --ext .md,.txt
#   IDENTICAL CONTENT groups  (keep one, delete the rest)
#   FORKS: same filename, different content  (the "which version is canonical?" case)
```

`locate` scans the git repos found under `--repos` (default: the folder's parent), fingerprints each repo's **tracked** files by normalized content, and classifies every file in the target folder as already-present, modified (same name, different content), or new. `dedup` groups every file under a root by content hash — a CRLF copy and its LF twin land in the *same* group, which a naïve byte hash would miss.

**Honest bounds.** Identity is whole-file content: a file with a one-character edit reads as a distinct version, and telling a *stale snapshot* from a genuine *fork* past "git-tracked wins, then most-complete" stays a judgment call. `--repos` reads each candidate repo's tracked files, so point it at a collection root, not your entire disk. Born from a real session that answered "is this folder already in a repo?" ~25 times by hand — see the `artifact-provenance-locate` research thread for the design and open questions.

## Adding pain points

Edit `src/git_coach/painpoints.toml`. Each entry:

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

v0.2.0 — plain-words coaching over a curated pain-point database (remotes, status, log, branch, diff, undo, stash, push; grows from real misses), plus content-addressed provenance (`locate` / `dedup`) for finding stranded and duplicate work across repos. Expand as real pain points appear in actual use.
