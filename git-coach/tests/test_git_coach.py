"""Tests for git-coach. Three surfaces: load, rank, state."""
from __future__ import annotations

import pytest

import git_coach
from git_coach import Painpoint, RepoState, load_painpoints, rank, SAFETY_LABELS


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
