"""The ``--live`` opt-in for ``live_kicad`` tests (#40).

Runs the real hooks from ``tests/conftest.py`` in a ``pytester`` sandbox over a
synthetic test file, so it never contacts KiCad. The live test's fixture
writes a file when it runs; its absence proves no live fixture was set up.
"""

from __future__ import annotations

import inspect

import pytest

from . import conftest

REASON = "live_kicad: pass --live to run (drives the open KiCad editor)"

SYNTHETIC = """
import pytest

@pytest.fixture
def live_fixture(pytestconfig):
    (pytestconfig.rootpath / "fixture_ran").write_text("yes")
    return 1

def test_plain():
    pass

@pytest.mark.live_kicad
def test_live(live_fixture):
    assert live_fixture == 1
"""


@pytest.fixture
def sandbox(pytester: pytest.Pytester) -> pytest.Pytester:
    hooks = (conftest.pytest_addoption, conftest.pytest_collection_modifyitems)
    pytester.makeconftest(
        "from __future__ import annotations\n\nimport pytest\n\n\n"
        + "\n\n".join(inspect.getsource(h) for h in hooks)
    )
    pytester.makeini("[pytest]\nmarkers =\n    live_kicad: synthetic live test\n")
    pytester.makepyfile(test_a=SYNTHETIC)
    return pytester


def fixture_ran(p: pytest.Pytester) -> bool:
    return (p.path / "fixture_ran").exists()


@pytest.mark.parametrize(
    ("args", "plain_passed"),
    [
        ([], 1),
        (["-m", "live_kicad"], 0),
        (["-m", ""], 1),
        (["test_a.py::test_live"], 0),
    ],
)
def test_live_skipped_without_flag(
    sandbox: pytest.Pytester, args: list[str], plain_passed: int
) -> None:
    result = sandbox.runpytest("-rs", *args)
    outcomes = result.parseoutcomes()
    assert outcomes["skipped"] == 1
    assert outcomes.get("passed", 0) == plain_passed
    result.stdout.fnmatch_lines([f"SKIPPED * {REASON}"])
    assert not fixture_ran(sandbox)


def test_live_runs_with_flag(sandbox: pytest.Pytester) -> None:
    result = sandbox.runpytest("--live", "-m", "live_kicad", "-rs")
    result.assert_outcomes(passed=1, deselected=1)
    assert fixture_ran(sandbox)


def test_flag_alone_runs_everything(sandbox: pytest.Pytester) -> None:
    result = sandbox.runpytest("--live", "-rs")
    result.assert_outcomes(passed=2)
    assert fixture_ran(sandbox)
