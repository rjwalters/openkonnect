"""SES-06 end to end: one real ``kct route`` wrapped in the handoff protocol.

Never runs in CI. Setup: start KiCad 10 with 'Enable KiCad API' on, and open
``tests/fixtures/board.kicad_pcb`` in the PCB editor. Then run
``uv run pytest --live -m live_kicad -rs``. Without a reachable KiCad this skips.

On KiCad 10.0.7 the editor text never byte-matches KiCad's own save, so the
board's dirty state is never ``false`` (measured in issue #23; see "Is the
editor text byte-identical to a KiCad save?" in ``docs/handoff-protocol.md``).
The test therefore passes ``allow_save=True``. Afterwards the fixture file is restored, the
``board.route.json`` record ``kct route`` leaves beside it is removed, and the
editor is re-synced to the restored file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openkonnect.tools.run_kct_handoff import find_kct, run_kct_handoff
from openkonnect.tools.session_status import session_status
from openkonnect.tools.sync_from_disk import sync_from_disk

from ..conftest import FIXTURE_BOARD, remove_kct_artifacts

pytestmark = pytest.mark.live_kicad


@pytest.fixture
def open_fixture(live_session):
    status = live_session.run_tool(session_status)
    boards = [d for d in status["documents"] if d["kind"] == "board"]
    match = [d for d in boards if os.path.basename(d["path"]) == os.path.basename(FIXTURE_BOARD)]
    if not match:
        names = [os.path.basename(d["path"]) for d in boards]
        pytest.skip(f"live_kicad: open {FIXTURE_BOARD} in KiCad (open boards: {names})")
    if find_kct() is None:
        pytest.skip("live_kicad: kct (kicad-tools) not found")
    path = Path(match[0]["path"])
    original = path.read_bytes()
    yield path
    try:
        path.write_bytes(original)
    finally:
        remove_kct_artifacts(path)
    live_session.run_tool(lambda s: sync_from_disk(s, "board", discard_unsaved=True))


def test_kct_route_handoff(live_session, open_fixture):
    r = run_kct_handoff(
        live_session,
        ["route", "{board}", "-o", "{board}"],
        allow_save=True,
        timeout_s=300,
    )
    assert r["ok"] is True, r
    steps = [s["name"] for s in r["steps"]]
    assert steps[:4] == ["session_status", "save_before_external_write", "kct", "check_command"]
    assert steps[-1] == "report"
    assert r["exit_code"] == 0
    if r["final_state"]["reloaded"]:
        assert "sync_from_disk" in steps
        # The editor now shows the routed file; its content hash was read back.
        assert r["final_state"]["editor_sha256"]
    else:
        assert r["steps"][4]["outcome"] == "skipped_reload"
