"""Tests for git-coach. Three surfaces: load, rank, state."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import git_coach
from git_coach import (
    Painpoint,
    RepoState,
    SAFETY_LABELS,
    dedup_scan,
    load_painpoints,
    locate_scan,
    normalized_hash,
    rank,
    _iter_files,
    _norm_exts,
    _repo_fingerprint,
)


# --- load -----------------------------------------------------------------

def test_load_bundled_painpoints_parses():
    pps = load_painpoints()
    assert len(pps) >= 12


def test_load_all_safety_labels_valid():
    pps = load_painpoints()
    for p in pps:
        assert p.safety in SAFETY_LABELS, f"{p.id} has invalid safety {p.safety!r}"


def test_load_all_ids_unique():
    pps = load_painpoints()
    ids = [p.id for p in pps]
    assert len(ids) == len(set(ids)), "duplicate painpoint ids"


def test_load_every_painpoint_has_nonempty_intents():
    pps = load_painpoints()
    for p in pps:
        assert p.intents, f"{p.id} has no intents"
        assert all(i.strip() for i in p.intents), f"{p.id} has empty intent string"


def test_load_invalid_safety_raises(tmp_path):
    bad = tmp_path / "bad.toml"
    bad.write_text(
        '[[painpoint]]\n'
        'id = "x.y"\n'
        'intents = ["foo"]\n'
        'command = "git foo"\n'
        'explanation = "does foo"\n'
        'safety = "nuclear"\n'
    )
    with pytest.raises(ValueError, match="invalid safety"):
        load_painpoints(bad)


# --- rank -----------------------------------------------------------------

def _pp(id_: str, *intents: str, safety: str = "readonly") -> Painpoint:
    return Painpoint(
        id=id_,
        intents=intents,
        command=f"git {id_}",
        explanation="",
        safety=safety,
        requires=(),
        warning=None,
    )


def test_rank_exact_match_wins():
    pps = [
        _pp("a", "show remotes"),
        _pp("b", "what branch am i on"),
        _pp("c", "what changed"),
    ]
    results = rank("show remotes", pps)
    assert results[0][0].id == "a"
    assert results[0][1] == 100.0


def test_rank_filters_below_threshold():
    pps = [_pp("a", "show remotes"), _pp("b", "stash pop")]
    results = rank("purple elephant", pps)
    assert results == []


def test_rank_returns_ordered_by_score():
    pps = [
        _pp("a", "show remotes"),
        _pp("b", "what branch am i on"),
    ]
    results = rank("show remotes", pps)
    scores = [s for _, s in results]
    assert scores == sorted(scores, reverse=True)


def test_rank_limit_respected():
    pps = [_pp(f"p{i}", "what changed") for i in range(10)]
    results = rank("what changed", pps, limit=3)
    assert len(results) == 3


# --- state ----------------------------------------------------------------

def test_state_satisfies_passes_when_all_checks_pass(monkeypatch):
    monkeypatch.setattr(git_coach, "_git", lambda *a: (0, "value"))
    state = RepoState()
    assert state.satisfies(("in-repo", "has-commits")) is True


def test_state_satisfies_fails_when_any_check_fails(monkeypatch):
    responses = {
        ("rev-parse", "--is-inside-work-tree"): (0, "true"),
        ("rev-parse", "HEAD"): (128, ""),  # no commits
    }
    monkeypatch.setattr(git_coach, "_git", lambda *a: responses.get(a, (1, "")))
    state = RepoState()
    assert state.satisfies(("in-repo", "has-commits")) is False


def test_state_caches_checks(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake(*a):
        calls.append(a)
        return (0, "ok")

    monkeypatch.setattr(git_coach, "_git", fake)
    state = RepoState()
    state.check("in-repo")
    state.check("in-repo")
    state.check("in-repo")
    assert len(calls) == 1  # cached after first


def test_state_unknown_check_raises(monkeypatch):
    monkeypatch.setattr(git_coach, "_git", lambda *a: (0, ""))
    state = RepoState()
    with pytest.raises(ValueError, match="unknown state check"):
        state.check("not-a-real-check")


def test_state_empty_requires_always_satisfies(monkeypatch):
    monkeypatch.setattr(git_coach, "_git", lambda *a: (1, ""))  # everything fails
    state = RepoState()
    assert state.satisfies(()) is True


# --- locate / dedup (content-addressed provenance) ------------------------
# Identity is normalized content, so line-endings must not create false
# differences -- the CRLF/LF cases below are the load-bearing invariant.

def test_normalized_hash_crlf_equals_lf(tmp_path):
    lf = tmp_path / "lf.txt"
    crlf = tmp_path / "crlf.txt"
    lf.write_bytes(b"alpha\nbeta\ngamma\n")
    crlf.write_bytes(b"alpha\r\nbeta\r\ngamma\r\n")
    assert normalized_hash(lf) == normalized_hash(crlf)


def test_normalized_hash_distinguishes_content(tmp_path):
    a = tmp_path / "a.txt"
    a.write_bytes(b"alpha\n")
    b = tmp_path / "b.txt"
    b.write_bytes(b"beta\n")
    assert normalized_hash(a) != normalized_hash(b)


def test_normalized_hash_missing_file_is_none(tmp_path):
    assert normalized_hash(tmp_path / "nope.txt") is None


def test_dedup_groups_identical_copies(tmp_path):
    (tmp_path / "d1").mkdir()
    (tmp_path / "d2").mkdir()
    for p in (tmp_path / "x.txt", tmp_path / "d1" / "y.txt", tmp_path / "d2" / "z.txt"):
        p.write_bytes(b"same content\n")
    (tmp_path / "other.txt").write_bytes(b"different\n")
    res = dedup_scan(tmp_path)
    assert res["n_files"] == 4
    assert len(res["duplicate_groups"]) == 1
    assert len(res["duplicate_groups"][0]["paths"]) == 3


def test_dedup_finds_forks_same_name(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "notes.md").write_bytes(b"v1\n")
    (tmp_path / "b" / "notes.md").write_bytes(b"v1 with more content\n")
    res = dedup_scan(tmp_path)
    forks = [g for g in res["fork_groups"] if g["name"] == "notes.md"]
    assert len(forks) == 1
    assert len(forks[0]["versions"]) == 2
    # versions are sorted longest-first (line-count proxy for "most complete")
    assert forks[0]["versions"][0]["lines"] >= forks[0]["versions"][1]["lines"]


def test_dedup_crlf_copies_count_as_identical(tmp_path):
    # Without normalization these hash differently and dedup would miss them.
    (tmp_path / "unix.txt").write_bytes(b"one\ntwo\n")
    (tmp_path / "dos.txt").write_bytes(b"one\r\ntwo\r\n")
    res = dedup_scan(tmp_path)
    assert len(res["duplicate_groups"]) == 1
    assert len(res["duplicate_groups"][0]["paths"]) == 2


def test_dedup_ext_filter(tmp_path):
    (tmp_path / "keep.md").write_bytes(b"m\n")
    (tmp_path / "skip.log").write_bytes(b"l\n")
    res = dedup_scan(tmp_path, _norm_exts(".md"))
    assert res["n_files"] == 1


def test_norm_exts_normalizes():
    assert _norm_exts(".md,txt") == (".md", ".txt")
    assert _norm_exts("  MD ") == (".md",)
    assert _norm_exts(None) is None
    assert _norm_exts("") is None


def _make_repo(path: Path, files: dict[str, bytes]) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    for rel, content in files.items():
        fp = path / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_bytes(content)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", "commit", "-qm", "init"],
        cwd=path, check=True,
    )
    return path


def test_locate_redundant(tmp_path):
    _make_repo(tmp_path / "repo", {"docs/notes.md": b"hello\nworld\n"})
    folder = tmp_path / "copy"
    folder.mkdir()
    (folder / "notes.md").write_bytes(b"hello\nworld\n")
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "REDUNDANT"
    assert res["stranded"] == []


def test_locate_ahead_lists_stranded(tmp_path):
    _make_repo(tmp_path / "repo", {"notes.md": b"hello\nworld\n"})
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "notes.md").write_bytes(b"hello\nworld\n")          # already in repo
    (folder / "brandnew.md").write_bytes(b"unique stranded work\n")  # not anywhere
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "AHEAD"
    assert "brandnew.md" in res["stranded"]
    assert "notes.md" not in res["stranded"]


def test_locate_orphan(tmp_path):
    _make_repo(tmp_path / "repo", {"notes.md": b"hello\n"})
    folder = tmp_path / "loose"
    folder.mkdir()
    (folder / "unrelated.md").write_bytes(b"nothing like the repo\n")
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "ORPHAN"


def test_locate_crlf_copy_is_redundant(tmp_path):
    # A CRLF copy of tracked content must still read as REDUNDANT, not AHEAD.
    _make_repo(tmp_path / "repo", {"notes.md": b"a\nb\nc\n"})
    folder = tmp_path / "dos"
    folder.mkdir()
    (folder / "notes.md").write_bytes(b"a\r\nb\r\nc\r\n")
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "REDUNDANT"


def test_locate_empty_folder(tmp_path):
    _make_repo(tmp_path / "repo", {"notes.md": b"hello\n"})
    folder = tmp_path / "empty"
    folder.mkdir()
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "EMPTY"


# --- verifier findings (windows-claude-55ef3834, re:b49c7cb1) --------------

def test_locate_subdir_of_repo_is_part_of_repo(tmp_path):
    # MUST-FIX (4c): a subdir of a tracked repo must NOT read as "safe to delete".
    _make_repo(tmp_path / "repo", {"sub/notes.md": b"hello\n"})
    res = locate_scan(tmp_path / "repo" / "sub", [tmp_path])
    assert res["verdict"] == "PART-OF-REPO"
    assert res["inside_repo"] == str((tmp_path / "repo").resolve())


def test_locate_name_clash_is_flagged_not_lineage(tmp_path):
    # Nit (3): a folder file that only SHARES A NAME with a repo file (different
    # content) is a name-clash, not a confirmed edit -- distinct status, no false lineage.
    _make_repo(tmp_path / "repo", {"README.md": b"the real repo readme\n", "keep.md": b"kept\n"})
    folder = tmp_path / "loose"
    folder.mkdir()
    (folder / "keep.md").write_bytes(b"kept\n")                       # same -> present
    (folder / "README.md").write_bytes(b"unrelated notes reusing a common name\n")
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "AHEAD"
    clash = [c for c in res["files"] if c["rel"] == "README.md"]
    assert clash and clash[0]["status"] == "name-clash"
    assert "README.md" in res["stranded"]


def test_locate_across_two_repos_is_redundant(tmp_path):
    # 4a: content split across two repos is still fully contained -> REDUNDANT, annotated.
    _make_repo(tmp_path / "repoA", {"a.md": b"alpha\n"})
    _make_repo(tmp_path / "repoB", {"b.md": b"bravo\n"})
    folder = tmp_path / "mix"
    folder.mkdir()
    (folder / "a.md").write_bytes(b"alpha\n")
    (folder / "b.md").write_bytes(b"bravo\n")
    res = locate_scan(folder, [tmp_path])
    assert res["verdict"] == "REDUNDANT"
    assert res["containing_repos"] == 2


def test_repo_fingerprint_warns_and_empties_on_non_repo(tmp_path, capsys):
    # Bonus: a repo whose git ls-files fails must warn, not silently shrink coverage.
    plain = tmp_path / "notarepo"
    plain.mkdir()
    fp = _repo_fingerprint(plain)
    assert fp["ok"] is False
    assert fp["hashes"] == set()
    assert "could not read tracked files" in capsys.readouterr().err


def test_iter_files_survives_junction_cycle(tmp_path):
    # 4d: os.walk follows Windows junctions; a self-referential loop must be pruned,
    # the one real file counted once, and the scan must terminate.
    import platform
    if platform.system() != "Windows":
        pytest.skip("junction reparse-point cycle is a Windows-specific case")
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.txt").write_bytes(b"x\n")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(real / "loop"), str(real)],
        capture_output=True, text=True, errors="replace",  # cmd's localized output may be non-ASCII
    )
    if made.returncode != 0:
        pytest.skip(f"could not create junction: {made.stderr.strip()}")
    hits = [f for f in _iter_files(tmp_path) if f.name == "f.txt"]
    assert len(hits) == 1  # counted once despite the cycle -- and did not hang
