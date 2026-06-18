"""git-coach — a deterministic git coach.

Maps plain-language intents to the exact git command, always shows the
manual command before offering to run it, and filters suggestions by
real-time repo state. No LLM, no network, no ambiguity.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from rapidfuzz import fuzz

__version__ = "0.1.0"

SAFETY_LABELS = ("readonly", "worktree", "history", "destructive")


@dataclass(frozen=True)
class Painpoint:
    id: str
    intents: tuple[str, ...]
    command: str
    explanation: str
    safety: str
    requires: tuple[str, ...]
    warning: str | None


def load_painpoints(path: Path | None = None) -> list[Painpoint]:
    if path is None:
        data = tomllib.loads((files("git_coach") / "painpoints.toml").read_text())
    else:
        data = tomllib.loads(path.read_text())
    out: list[Painpoint] = []
    for p in data.get("painpoint", []):
        if p["safety"] not in SAFETY_LABELS:
            raise ValueError(f"{p['id']}: invalid safety tier {p['safety']!r}")
        out.append(
            Painpoint(
                id=p["id"],
                intents=tuple(p["intents"]),
                command=p["command"],
                explanation=p["explanation"],
                safety=p["safety"],
                requires=tuple(p.get("requires", [])),
                warning=p.get("warning"),
            )
        )
    return out


def _git(*args: str) -> tuple[int, str]:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


class RepoState:
    """Lazy, cached repo-state checks. Each runs at most once per invocation."""

    _CHECKS = {
        "in-repo":       lambda: _git("rev-parse", "--is-inside-work-tree")[0] == 0,
        "has-commits":   lambda: _git("rev-parse", "HEAD")[0] == 0,
        "has-upstream":  lambda: _git("rev-parse", "--abbrev-ref", "@{u}")[0] == 0,
        "dirty":         lambda: bool(_git("status", "--porcelain")[1]),
        "detached-head": lambda: _git("symbolic-ref", "-q", "HEAD")[0] != 0,
        "has-stash":     lambda: bool(_git("stash", "list")[1]),
        "has-remote":    lambda: bool(_git("remote")[1]),
    }

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    def check(self, name: str) -> bool:
        if name not in self._cache:
            if name not in self._CHECKS:
                raise ValueError(f"unknown state check: {name!r}")
            self._cache[name] = self._CHECKS[name]()
        return self._cache[name]

    def satisfies(self, requires: tuple[str, ...]) -> bool:
        return all(self.check(r) for r in requires)


def _score(query: str, intent: str) -> float:
    # token_set_ratio: 100 iff all query tokens appear in the intent (order-free).
    # partial_ratio: best substring alignment, helps with "status" vs "whats staged".
    # Average of the two balances word-overlap against substring similarity.
    return (fuzz.token_set_ratio(query, intent) + fuzz.partial_ratio(query, intent)) / 2


def rank(
    query: str,
    painpoints: list[Painpoint],
    limit: int = 5,
    threshold: float = 65.0,
) -> list[tuple[Painpoint, float]]:
    scored: list[tuple[Painpoint, float]] = []
    for p in painpoints:
        best = max(_score(query, intent) for intent in p.intents)
        if best >= threshold:
            scored.append((p, best))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def display(p: Painpoint) -> None:
    tier = {
        "readonly":    "READ-ONLY",
        "worktree":    "WORKTREE",
        "history":     "HISTORY",
        "destructive": "DESTRUCTIVE",
    }[p.safety]
    print()
    print(f"  Command:  {p.command}")
    print(f"  Why:      {p.explanation}")
    print(f"  Safety:   {tier}")
    if p.warning:
        print(f"  Warning:  {p.warning}")
    print()


def confirm(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def run(p: Painpoint) -> int:
    if "<" in p.command or ">" in p.command:
        print("This command has a placeholder - edit and run it yourself.")
        return 0
    if p.safety == "readonly":
        pass
    elif p.safety in ("worktree", "history"):
        if not confirm("Run this? [y/N] "):
            return 0
    elif p.safety == "destructive":
        token = p.id.rsplit(".", 1)[-1]
        try:
            ans = input(f"DESTRUCTIVE. Type {token!r} to confirm: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if ans != token:
            print("Aborted.")
            return 0
    print(f"$ {p.command}")
    return subprocess.call(p.command, shell=True)


COACH_BANNER = (
    "git-coach  -  a deterministic git coach.\n"
    "Type what you want git to do, in plain words; I'll show you the exact command.\n"
    "  examples:  list the files of the repo  |  compare to main  |  undo my last commit\n"
    "  commands:  help  (show this)     quit / exit  (leave)\n"
    "Nothing runs without showing you the command first."
)


def answer(query: str, eligible: list[Painpoint], do_run: bool) -> int:
    """Resolve one plain-words query: rank, show the command, optionally run it."""
    results = rank(query, eligible)

    if not results:
        print(f"No match for: {query!r}")
        return 1

    top, top_score = results[0]
    runaway = len(results) == 1 or top_score - results[1][1] >= 15

    if runaway:
        print(f"Match: {top.id}  (score {int(top_score)})")
        display(top)
        return run(top) if do_run else 0

    print(f"Multiple matches for: {query!r}")
    for i, (p, s) in enumerate(results, 1):
        print(f"  {i}. {p.id}  -  {p.command}  (score {int(s)})")
    try:
        pick = input("Pick [1]: ").strip() or "1"
        idx = int(pick) - 1
        if not 0 <= idx < len(results):
            return 1
    except (ValueError, EOFError, KeyboardInterrupt):
        print()
        return 1

    chosen = results[idx][0]
    display(chosen)
    return run(chosen) if do_run else 0


RISKY = ("--force", "reset --hard", "clean -f", "filter-branch", "rebase", "push -f")


def run_literal(cmd: str, want_run: bool) -> int:
    """Handle a line that is already a literal git command, not a plain-words intent."""
    if not want_run:
        print(f"\n  That is already a git command:  {cmd}")
        print(f"  To run it from here:  run {cmd}   (or start git-coach with --run)\n")
        return 0
    risky = any(t in cmd for t in RISKY)
    if risky and not confirm("This can rewrite or lose work. Run it anyway? [y/N] "):
        return 0
    print(f"$ {cmd}")
    return subprocess.call(cmd, shell=True)


def dispatch(line: str, eligible: list[Painpoint], do_run: bool) -> int:
    """Route one line: an explicit 'run ...', a literal 'git ...' command, or a plain-words query."""
    forced = False
    if line.lower().startswith("run "):
        line = line[4:].strip()
        forced = True
    if line == "git" or line.startswith("git "):
        return run_literal(line, do_run or forced)
    return answer(line, eligible, do_run)


def repl(eligible: list[Painpoint], do_run: bool) -> int:
    """Interactive coach: ask in plain words, get the command, learn as you go."""
    print(COACH_BANNER)
    while True:
        try:
            line = input("\ngit-coach> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line.lower() in ("quit", "exit", "q"):
            return 0
        if line.lower() in ("help", "?", "h"):
            print(COACH_BANNER)
            continue
        dispatch(line, eligible, do_run)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="git-coach",
        description="A deterministic git coach.",
    )
    parser.add_argument(
        "query", nargs="*",
        help="what you want git to do, in plain words (omit to start interactive coach mode)",
    )
    parser.add_argument("--run", action="store_true", help="offer to run the chosen command")
    parser.add_argument("--file", type=Path, default=None, help="path to a custom painpoints.toml")
    parser.add_argument("--version", action="version", version=f"git-coach {__version__}")
    args = parser.parse_args(argv)

    try:
        painpoints = load_painpoints(args.file)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading painpoints: {e}", file=sys.stderr)
        return 2

    state = RepoState()
    if not state.check("in-repo"):
        print("Not inside a git repository.", file=sys.stderr)
        return 2

    eligible = [p for p in painpoints if state.satisfies(p.requires)]

    # No query -> interactive coach mode (bare `git-coach`, or `git-coach --run`).
    if not args.query:
        return repl(eligible, args.run)

    return dispatch(" ".join(args.query), eligible, args.run)
