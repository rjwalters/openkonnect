"""SES-06 end to end: one real ``kct route`` wrapped in the handoff protocol.

Never runs in CI. Setup: start KiCad 10 with 'Enable KiCad API' on, and open
``tests/fixtures/selection.kicad_pcb`` in the PCB editor. Then run
``uv run pytest --live -m live_kicad -rs``. Without a reachable KiCad this skips.

The fixture is ``selection.kicad_pcb`` because its SIG net is unrouted, so
``kct route`` changes the file and the reload path runs; ``board.kicad_pcb`` is
fully routed and ``kct route`` leaves it unchanged (measured, issue #37). The
fixture was not written by KiCad, so as opened the editor text differs from
the file and the dirty state is ``unknown``: the test passes ``allow_save=True``.
After KiCad's save, the editor text matches the file by the strict token
comparison (KiCad 10.0.7, issue #37; see "Is the editor text byte-identical to
a KiCad save?" in ``docs/handoff-protocol.md``), so the handoff takes its
snapshot and reloads without ``discard_unsaved``. On a KiCad version not on the
measured allow-list there is no snapshot, and the test expects the reload to be
refused with ``dirty_state_unknown``. Afterwards the fixture file is restored, the
``selection.route.json`` record ``kct route`` leaves beside it is removed, and the
editor is re-synced to the restored file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openkonnect.kicad_text import canonical_comparison_applies
from openkonnect.tools.run_kct_handoff import find_kct, run_kct_handoff
from openkonnect.tools.session_status import session_status
from openkonnect.tools.sync_from_disk import sync_from_disk

from ..conftest import SELECTION_BOARD, remove_kct_artifacts

pytestmark = pytest.mark.live_kicad


#: Files ``kct route`` writes beside ``selection.kicad_pcb`` besides the route
#: record (``remove_kct_artifacts``); ``{stem}`` is the board's stem.
KCT_ROUTE_OUTPUTS = (
    "fab_profile.json",
    "net_class_map.json",
    "{stem}.access_witness.json",
    "{stem}.kicad_dru",
    "{stem}.routing_plan.json",
)


def _remove_new_files(board: Path, before: set[str]) -> None:
    """Delete the known ``kct route`` outputs that appeared beside ``board``.

    Only names in :data:`KCT_ROUTE_OUTPUTS` that were not there before the test
    are removed, so nothing else created in the directory meanwhile is touched.
    """
    for pattern in KCT_ROUTE_OUTPUTS:
        name = pattern.format(stem=board.stem)
        if name not in before:
            (board.parent / name).unlink(missing_ok=True)


@pytest.fixture
def open_fixture(live_session):
    status = live_session.run_tool(session_status)
    boards = [d for d in status["documents"] if d["kind"] == "board"]
    match = [d for d in boards if os.path.basename(d["path"]) == os.path.basename(SELECTION_BOARD)]
    if not match:
        names = [os.path.basename(d["path"]) for d in boards]
        pytest.skip(f"live_kicad: open {SELECTION_BOARD} in KiCad (open boards: {names})")
    if find_kct() is None:
        pytest.skip("live_kicad: kct (kicad-tools) not found")
    path = Path(match[0]["path"])
    original = path.read_bytes()
    before = {p.name for p in path.parent.iterdir()}
    yield path
    try:
        path.write_bytes(original)
    finally:
        remove_kct_artifacts(path)
        _remove_new_files(path, before)
    live_session.run_tool(lambda s: sync_from_disk(s, "board", discard_unsaved=True))


def test_kct_route_handoff(live_session, open_fixture):
    version = live_session.run_tool(session_status)["kicad_version"]
    r = run_kct_handoff(
        live_session,
        ["route", "{board}", "-o", "{board}"],
        allow_save=True,
        timeout_s=300,
    )
    steps = {s["name"]: s for s in r["steps"]}
    names = [s["name"] for s in r["steps"]]
    assert names[:4] == ["session_status", "save_before_external_write", "kct", "check_command"]
    assert r["exit_code"] == 0, r
    # kct route must change the fixture, or the reload path is not exercised.
    assert steps["kct"]["file_changed"] is True, r
    if not canonical_comparison_applies(version):
        # Unmeasured KiCad: no snapshot, so the reload is refused (issue #37).
        assert r["ok"] is False, r
        assert r["error"]["code"] == "dirty_state_unknown"
        return
    assert r["ok"] is True, r
    assert names == [
        "session_status",
        "save_before_external_write",
        "kct",
        "check_command",
        "check_file_changed",
        "sync_from_disk",
        "report",
    ]
    save = steps["save_before_external_write"]
    assert save["outcome"] == "saved"
    assert save["editor_matches_file"] is True
    assert save["match_basis"] == "canonical"
    sync = steps["sync_from_disk"]
    assert sync["outcome"] == "reloaded"
    assert sync["snapshot_matched"] is True
    assert sync["discarded_unsaved"] is False
    assert r["final_state"]["reloaded"] is True
    # The editor now shows the routed file; its content hash was read back.
    assert r["final_state"]["editor_sha256"]
