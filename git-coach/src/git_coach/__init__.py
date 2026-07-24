"""git-coach — a deterministic git multitool with a coaching mode.

Two kinds of help, both deterministic and offline:
  - coaching mode: plain-language intent -> the exact git command, shown before
    it runs and filtered by real-time repo state (the "how do I ..." problem, for
    when you know what you want but not the syntax).
  - power commands: things no single git command exposes -- content-addressed
    provenance (locate / dedup: which repo is this folder, what is duplicated).

No LLM, no network, no nondeterminism: it gives you answers, not guesses.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from rapidfuzz import fuzz

__version__ = "0.2.0"

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
    "git-coach  -  coaching mode (a deterministic git multitool).\n"
    "Type what you want git to do, in plain words; I'll show you the exact command.\n"
    "  examples:  list the files of the repo  |  compare to main  |  undo my last commit\n"
    "  commands:  help  (show this)     quit / exit  (leave)\n"
    "  also (outside this prompt):  git-coach locate <folder>   git-coach dedup <root>\n"
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


# ==========================================================================
# locate / dedup -- content-addressed repo provenance.
#
# A file's identity is its (normalized) CONTENT, not its name or location.
# The whole feature is one primitive -- normalize(CRLF->LF) -> sha256 -> group --
# applied two ways:
#   dedup  <root>    which files under here are byte-identical duplicates, or
#                    same-named forks (the "which version is canonical?" case)?
#   locate <folder>  which repo is this folder, and is it redundant / ahead / orphan?
#
# No LLM, no fuzzy matching -- git already content-addresses every file it has
# ever seen, so provenance is a set operation, not a search. Normalization is
# LOAD-BEARING: files identical but for line endings (CRLF vs LF) must hash the
# same, or every such pair reads as a false difference (proven on real specimens).
# Honest bound: identity here is whole-FILE content; a smarter per-region check
# is a later upgrade. Canonical-selection past "git-tracked > superset" stays a
# heuristic + operator judgment. See the artifact-provenance-locate research thread.
# ==========================================================================

_HASH_MAX_BYTES = 10 * 1024 * 1024  # skip files above this (unlikely to be text artifacts)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", ".mypy_cache"}


def normalized_hash(path: Path) -> str | None:
    """sha256 of the file with CRLF/CR flattened to LF. None if unreadable or too large."""
    try:
        if path.stat().st_size > _HASH_MAX_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _norm_exts(raw: str | None) -> tuple[str, ...] | None:
    """'.md,txt' -> ('.md', '.txt'); None/empty -> None."""
    if not raw:
        return None
    out = []
    for e in raw.split(","):
        e = e.strip().lower()
        if not e:
            continue
        out.append(e if e.startswith(".") else "." + e)
    return tuple(out) or None


def _walk_safe(root: Path):
    """os.walk with reparse-point / junction cycle protection + _SKIP_DIRS pruning.

    os.walk follows Windows junctions even with followlinks=False (they are reparse
    points, not symlinks), so a junction that points back up its own tree loops until
    MAX_PATH cuts it -- inflating file counts (a 1-file dir scanned as 64). Prune any
    directory whose real identity (st_dev, st_ino) was already entered this walk.
    """
    visited: set[tuple[int, int]] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        try:
            st = os.stat(dirpath)
        except OSError:
            dirnames[:] = []
            continue
        key = (st.st_dev, st.st_ino)
        if key in visited:
            dirnames[:] = []      # already walked this real dir -> a cycle, stop here
            continue
        visited.add(key)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        yield dirpath, dirnames, filenames


def _iter_files(root: Path, exts: tuple[str, ...] | None = None):
    for dirpath, _dirnames, filenames in _walk_safe(root):
        for name in filenames:
            if exts and not name.lower().endswith(exts):
                continue
            yield Path(dirpath) / name


def _line_count(path: Path) -> int:
    try:
        return path.read_bytes().count(b"\n") + 1
    except OSError:
        return 0


def dedup_scan(root: Path, exts: tuple[str, ...] | None = None) -> dict:
    """Group files under `root` by normalized content-hash.

    Returns identical-content groups (same hash, >=2 files -> safe to keep one) and
    fork groups (same basename, >=2 distinct contents -> the canonical-version question).
    """
    by_hash: dict[str, list[Path]] = {}
    by_name: dict[str, dict[str, list[Path]]] = {}
    n = 0
    for fp in _iter_files(root, exts):
        h = normalized_hash(fp)
        if h is None:
            continue
        n += 1
        by_hash.setdefault(h, []).append(fp)
        by_name.setdefault(fp.name, {}).setdefault(h, []).append(fp)

    duplicate_groups = [
        {"hash": h, "paths": sorted(str(p) for p in paths)}
        for h, paths in by_hash.items() if len(paths) > 1
    ]
    duplicate_groups.sort(key=lambda g: -len(g["paths"]))

    fork_groups = []
    for name, hashes in by_name.items():
        if len(hashes) > 1:  # one basename, more than one distinct content
            versions = []
            for h, paths in hashes.items():
                rep = min(paths, key=str)
                versions.append({
                    "hash": h, "lines": _line_count(rep),
                    "copies": len(paths), "path": str(rep),
                })
            versions.sort(key=lambda v: -v["lines"])
            fork_groups.append({"name": name, "versions": versions})
    fork_groups.sort(key=lambda g: -len(g["versions"]))

    return {"root": str(root), "n_files": n,
            "duplicate_groups": duplicate_groups, "fork_groups": fork_groups}


def _find_repos(root: Path, max_depth: int = 4) -> list[Path]:
    """Git repos (dirs containing .git) under `root`, bounded depth, not descending into repos."""
    root = root.resolve()
    base = len(root.parts)
    repos = []
    visited: set[tuple[int, int]] = set()
    for dirpath, dirnames, _ in os.walk(root):
        try:
            st = os.stat(dirpath)
        except OSError:
            dirnames[:] = []
            continue
        key = (st.st_dev, st.st_ino)
        if key in visited:
            dirnames[:] = []       # junction/symlink cycle -- do not re-descend
            continue
        visited.add(key)
        d = Path(dirpath)
        if (d / ".git").exists():
            repos.append(d)
            dirnames[:] = []  # a repo's own subdirs are part of it -- don't recurse
            continue
        if len(d.parts) - base >= max_depth:
            dirnames[:] = []
        dirnames[:] = [x for x in dirnames if x not in _SKIP_DIRS]
    return repos


def _repo_fingerprint(repo: Path) -> dict:
    """Normalized-hash fingerprint of a repo's TRACKED files (hashes + basenames).

    `ok` is False when `git ls-files` failed: the repo then contributes NOTHING to
    provenance. The failure direction is safe (an affected folder reads as ORPHAN/AHEAD,
    never a false REDUNDANT) but it silently shrinks coverage -- so we warn, loudly.
    """
    code, out = _git("-C", str(repo), "ls-files", "-z")
    hashes: set[str] = set()
    basenames: set[str] = set()
    if code != 0:
        print(f"warning: could not read tracked files of {repo} "
              f"(git ls-files exit {code}); it will NOT count toward provenance.",
              file=sys.stderr)
        return {"repo": str(repo), "hashes": hashes, "basenames": basenames, "ok": False}
    for rel in out.split("\0"):
        if not rel:
            continue
        h = normalized_hash(repo / rel)
        if h is None:
            continue
        hashes.add(h)
        basenames.add(Path(rel).name)
    return {"repo": str(repo), "hashes": hashes, "basenames": basenames, "ok": True}


def locate_scan(folder: Path, repo_roots: list[Path]) -> dict:
    """Classify `folder` against the repos found under `repo_roots`, by content-hash.

    Verdict:
      PART-OF-REPO  the folder lives INSIDE a scanned repo's working tree -> it IS
                    part of that repo, not a loose copy; deleting it deletes repo content
      REDUNDANT     every file is already in your repos  -> safe to delete the folder
      AHEAD         folder holds content no repo has     -> stranded work, rescue it
      ORPHAN        none of it is in any scanned repo    -> it needs a home
      EMPTY         no hashable files
    """
    files: dict[str, tuple[str, str]] = {}
    for fp in _iter_files(folder):
        h = normalized_hash(fp)
        if h is None:
            continue
        files[str(fp.relative_to(folder))] = (h, fp.name)

    repos: list[dict] = []
    repo_paths: list[Path] = []
    seen: set[str] = set()
    for r in repo_roots:
        for repo in _find_repos(r):
            key = str(repo)
            if key in seen:
                continue
            seen.add(key)
            repo_paths.append(repo)
            repos.append(_repo_fingerprint(repo))
    repos_failed = sum(1 for rc in repos if not rc.get("ok", True))

    # Must-fix guard: if the folder IS a scanned repo, or lives inside one, it is PART
    # of that repo -- never advise deleting it as a "loose copy" (that deletes repo content).
    folder_res = folder.resolve()
    inside_repo = None
    for repo in repo_paths:
        rr = repo.resolve()
        if folder_res == rr or folder_res.is_relative_to(rr):
            inside_repo = str(rr)
            break

    base = {"folder": str(folder), "n_files": len(files), "repos_scanned": len(repos),
            "repos_failed": repos_failed, "inside_repo": inside_repo, "containing_repos": 0}

    if not files:
        return {**base, "best_repo": None, "best_matches": 0, "verdict": "EMPTY",
                "files": [], "stranded": []}

    def matches(rc: dict) -> int:
        return sum(1 for (h, _) in files.values() if h in rc["hashes"])

    best = max(repos, key=matches) if repos else None
    best_n = matches(best) if best else 0
    any_hashes = set().union(*(rc["hashes"] for rc in repos)) if repos else set()
    containing_repos = sum(1 for rc in repos if matches(rc) > 0)

    classified = []
    stranded = []
    for rel, (h, name) in sorted(files.items()):
        if best and h in best["hashes"]:
            status = "same"
        elif h in any_hashes:
            status = "elsewhere"        # content lives in a different repo (still safe)
        elif best and name in best["basenames"]:
            status = "name-clash"       # same filename in best repo, DIFFERENT content --
                                        # could be your edit OR a coincidental name match
        else:
            status = "new"
        classified.append({"rel": rel, "status": status})
        if status in ("name-clash", "new"):
            stranded.append(rel)

    if inside_repo:
        verdict = "PART-OF-REPO"        # precedence: never "safe to delete" a repo subdir
    elif len(stranded) == len(files):
        verdict = "ORPHAN"
    elif stranded:
        verdict = "AHEAD"
    else:
        verdict = "REDUNDANT"

    return {**base, "containing_repos": containing_repos,
            "best_repo": best["repo"] if best else None, "best_matches": best_n,
            "verdict": verdict, "files": classified, "stranded": stranded}


def cmd_dedup(rest: list[str], exts: tuple[str, ...] | None) -> int:
    if not rest:
        print("usage: git-coach dedup <root> [--ext .md,.txt]", file=sys.stderr)
        return 2
    root = Path(rest[0]).expanduser()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2
    res = dedup_scan(root, exts)
    print(f"\nDEDUP  {res['root']}  ({res['n_files']} files scanned)\n")
    dg = res["duplicate_groups"]
    if dg:
        redundant = sum(len(g["paths"]) - 1 for g in dg)
        print(f"IDENTICAL CONTENT -- {len(dg)} group(s), {redundant} redundant copy(ies):")
        for g in dg:
            print(f"  [{g['hash'][:8]}]  {len(g['paths'])} copies -> keep 1, delete {len(g['paths']) - 1}:")
            for p in g["paths"]:
                print(f"       {p}")
        print()
    fg = res["fork_groups"]
    if fg:
        print(f"FORKS -- same name, different content ({len(fg)}), need review:")
        for g in fg:
            print(f"  {g['name']}: {len(g['versions'])} versions")
            for v in g["versions"]:
                tag = f"  x{v['copies']}" if v["copies"] > 1 else ""
                print(f"       {v['lines']:>6} L  [{v['hash'][:8]}]{tag}  {v['path']}")
        print()
    if not dg and not fg:
        print("No duplicates or forks found.\n")
    return 0


def cmd_locate(rest: list[str], repos_root: Path | None) -> int:
    if not rest:
        print("usage: git-coach locate <folder> [--repos <root>]", file=sys.stderr)
        return 2
    folder = Path(rest[0]).expanduser()
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        return 2
    roots = [repos_root.expanduser()] if repos_root else [folder.resolve().parent]
    res = locate_scan(folder, roots)
    print(f"\nLOCATE  {res['folder']}  ({res['n_files']} files)")
    scanned = f"  scanned {res['repos_scanned']} repo(s) under {roots[0]}"
    if res.get("repos_failed"):
        scanned += f"  ({res['repos_failed']} unreadable, excluded -- see warnings above)"
    print(scanned)
    if res["best_repo"]:
        print(f"  best match: {res['best_repo']}  ({res['best_matches']}/{res['n_files']} files present)")
    print()
    v = res["verdict"]
    if v == "EMPTY":
        print("  Verdict: EMPTY -- no hashable files in this folder.\n")
    elif v == "PART-OF-REPO":
        print("  Verdict: PART-OF-REPO -- this folder lives INSIDE the tracked repo:")
        print(f"           {res['inside_repo']}")
        print("           It is part of that repo's working tree, NOT a loose copy -- do")
        print("           not delete it as redundant.")
        if res["stranded"]:
            print(f"           Note: {len(res['stranded'])} file(s) here are not committed to it:")
            for c in res["files"]:
                if c["status"] in ("name-clash", "new"):
                    print(f"             {c['rel']}")
        print()
    elif v == "REDUNDANT":
        spread = ""
        if res.get("containing_repos", 0) > 1:
            spread = f" (spread across {res['containing_repos']} of your repos)"
        print(f"  Verdict: REDUNDANT -- every file already lives in your repos{spread}.")
        print("           Safe to delete this folder (nothing unique is here).\n")
    elif v == "ORPHAN":
        print("  Verdict: ORPHAN -- none of this folder's content is in any scanned repo.")
        print("           It needs a home. (Expected a match? widen with --repos <root>.)\n")
    else:  # AHEAD
        print(f"  Verdict: AHEAD -- this folder holds {len(res['stranded'])} file(s) no repo has:")
        for c in res["files"]:
            if c["status"] == "new":
                print(f"       [new       ] {c['rel']}")
            elif c["status"] == "name-clash":
                print(f"       [name-clash] {c['rel']}  (a DIFFERENT file of this name is in the "
                      "best-match repo -- your edit, or coincidence? diff before assuming)")
        print("  Next: the [new] files have no home yet; the [name-clash] files share a name with")
        print(f"        a file in {res['best_repo']} but differ -- compare them before assuming")
        print("        they belong together.\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="git-coach",
        description="A deterministic git multitool with a coaching mode.",
    )
    parser.add_argument(
        "query", nargs="*",
        help="what you want git to do, in plain words (omit to start interactive coach mode)",
    )
    parser.add_argument("--run", action="store_true", help="offer to run the chosen command")
    parser.add_argument("--file", type=Path, default=None, help="path to a custom painpoints.toml")
    parser.add_argument("--repos", type=Path, default=None,
                        help="locate: root to search for candidate repos (default: the folder's parent)")
    parser.add_argument("--ext", type=str, default=None,
                        help="dedup: comma-separated extensions to limit the scan, e.g. .md,.txt")
    parser.add_argument("--version", action="version", version=f"git-coach {__version__}")
    args = parser.parse_args(argv)

    # Content-provenance subcommands operate on arbitrary paths, so they route
    # here -- BEFORE the in-repo gate below (they don't need the cwd to be a repo).
    if args.query and args.query[0] == "dedup":
        return cmd_dedup(args.query[1:], _norm_exts(args.ext))
    if args.query and args.query[0] == "locate":
        return cmd_locate(args.query[1:], args.repos)

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
