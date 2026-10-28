"""Files KiCad and ``kct`` leave in ``tests/fixtures/`` stay out of ``git status`` (#33).

The ignore checks ask git itself (``git check-ignore``, ``git ls-files -ci``)
against the repo's real ``.gitignore``; they skip when git or a work tree is
unavailable (for example, in an unpacked sdist). No KiCad needed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import remove_kct_artifacts

REPO = Path(__file__).resolve().parent.parent

IGNORED = [
    "tests/fixtures/board.kicad_prl",
    "tests/fixtures/board.kicad_pro",
    "tests/fixtures/board.route.json",
    "tests/fixtures/~board.kicad_pcb.lck",
    "tests/fixtures/~board.kicad_pro.lck",
    "tests/fixtures/~selection.kicad_pcb.lck",
    "tests/fixtures/~selection.kicad_pro.lck",
    "anywhere/x.kicad_prl",
    "anywhere/~x.kicad_pcb.lck",
]

NOT_IGNORED = [
    "tests/fixtures/board.kicad_pcb",
    "tests/fixtures/selection.kicad_pcb",
    "boards/x/x.kicad_pro",
]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture(scope="module")
def git_tree() -> None:
    if shutil.which("git") is None:
        pytest.skip("git not available")
    r = _git("rev-parse", "--is-inside-work-tree")
    if r.returncode != 0 or r.stdout.strip() != "true":
        pytest.skip("not a git work tree")


@pytest.mark.parametrize("path", IGNORED)
def test_kicad_and_kct_files_are_ignored(git_tree, path):
    r = _git("check-ignore", "-v", "--no-index", path)
    assert r.returncode == 0, f"{path} is not ignored: {r.stdout}{r.stderr}"
    assert r.stdout.strip(), path


@pytest.mark.parametrize("path", NOT_IGNORED)
def test_sources_and_real_projects_are_not_ignored(git_tree, path):
    r = _git("check-ignore", "-v", "--no-index", path)
    assert r.returncode == 1, f"{path} is ignored: {r.stdout}{r.stderr}"


def test_no_tracked_file_is_ignored(git_tree):
    r = _git("ls-files", "-ci", "--exclude-standard")
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_remove_kct_artifacts_deletes_only_the_route_record(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    (tmp_path / "board.kicad_pro").write_text("{}")
    (tmp_path / "board.route.json").write_text("{}")
    remove_kct_artifacts(board)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["board.kicad_pcb", "board.kicad_pro"]


def test_remove_kct_artifacts_is_a_no_op_when_absent(tmp_path):
    board = tmp_path / "board.kicad_pcb"
    board.write_text("(kicad_pcb)")
    remove_kct_artifacts(board)
    assert [p.name for p in tmp_path.iterdir()] == ["board.kicad_pcb"]
