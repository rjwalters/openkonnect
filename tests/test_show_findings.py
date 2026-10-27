"""SES-09 ``show_findings`` and the SES-10 round trip against a fake PCB editor.

Findings come from the installed kicad-tools: either its model objects built
directly, or the JSON a real ``kct`` run prints for ``tests/fixtures/
selection.kicad_pcb``. No finding dict is copied into this file except the
deliberately malformed ones.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from kicad_tools.analysis.net_status import NetStatus, PadInfo
from kicad_tools.explain.mistakes import Mistake, MistakeCategory
from kicad_tools.validate.violations import DRCViolation

from openkonnect.session import file_state
from openkonnect.tools import findings as F
from openkonnect.tools.get_selection import get_selection
from openkonnect.tools.run_kct_handoff import find_kct
from openkonnect.tools.show_findings import show_findings

from .conftest import SELECTION_BOARD, EditorFake, make_session


def show(fake, findings, **kw):
    session = make_session(fake)
    return session.run_tool(lambda s: show_findings(s, findings, **kw))


def selected(fake):
    r = make_session(fake).run_tool(get_selection)
    assert r["ok"], r
    return r


def kct_json(*argv: str) -> dict:
    kct = find_kct()
    if kct is None:
        pytest.skip("kct (kicad-tools) not found")
    out = subprocess.run(
        [kct, *argv], capture_output=True, text=True, timeout=120, check=False
    ).stdout
    return json.loads(out)


def courtyard(ref: str, x: float, y: float) -> DRCViolation:
    return DRCViolation(
        rule_id="courtyard_missing",
        severity="warning",
        message=f"{ref} has no courtyard",
        location=(x, y),
        layer="F.Cu",
        items=(ref,),
    )


# -- the extractor reads kicad-tools' models -----------------------------------


def test_every_supported_model_loads_from_installed_kicad_tools():
    status = F.models_status()
    assert {s["source"] for s in status} == {
        "kct check",
        "kct drc",
        "kct detect-mistakes",
        "kct net-status",
    }
    assert all(s["enabled"] for s in status), status


def test_real_model_objects_and_their_json_resolve_the_same():
    obj = courtyard("R2", 120.0, 60.0)
    a = F.match(obj)
    b = F.match(obj.to_dict())
    assert a is b and a.source == "kct check"
    assert F.locators(obj, a) == F.locators(obj.to_dict(), b)

    fake = EditorFake()
    r = show(fake, [obj])
    assert r["ok"] is True, r
    [f] = r["findings"]
    assert f["status"] == "resolved"
    assert f["resolved"]["footprints"] == ["R2"]
    assert f["action"] == "selected"
    # The point is checked against the resolved footprint, in the file frame.
    [p] = f["points"]
    assert p["frame"] == "file" and p["used_for"] == "check"
    assert p["within_resolved_items"] is True
    assert selected(fake)["footprints"] == ["R2"]


def test_net_status_objects_give_pads_and_board_frame_points():
    status = NetStatus(
        net_number=1,
        net_name="VIN",
        connected_pads=[
            PadInfo(reference="R1", pad_number="1", position=(9.0, 10.0), is_connected=True),
            PadInfo(reference="R2", pad_number="1", position=(19.0, 10.0), is_connected=True),
        ],
    )
    fake = EditorFake()
    r = show(fake, [status])
    assert r["ok"] is True, r
    [f] = r["findings"]
    assert f["source"] == "kct net-status"
    assert f["resolved"]["pads"] == ["R1.1", "R2.1"]
    assert f["resolved"]["nets"] == ["VIN"]
    # VIN is routed, so the net brings its three track segments.
    assert f["resolved"]["item_count"] == 5
    for p in f["points"]:
        assert p["frame"] == "board"
        assert p["within_resolved_items"] is True
    assert f["points"][0]["file_mm"] == {"x_mm": 109.0, "y_mm": 60.0}


def test_mistake_location_has_no_stated_frame_and_is_not_used():
    m = Mistake(
        category=MistakeCategory.POWER_TRACE,
        severity="warning",
        title="Power trace too narrow",
        components=["VIN"],
        explanation="",
        fix_suggestion="",
        location=(9.0, 10.0),
    )
    fake = EditorFake()
    r = show(fake, [m])
    [f] = r["findings"]
    assert f["status"] == "resolved"
    assert f["resolved"]["nets"] == ["VIN"]
    [p] = f["points"]
    assert p["used_for"] == "none" and p["reason"] == "frame_not_stated"


def test_unrecognised_finding_is_invalid_argument_naming_its_index():
    fake = EditorFake()
    r = show(fake, [courtyard("R1", 110, 60).to_dict(), {"severity": "error", "where": "here"}])
    assert r["ok"] is False
    assert r["error"]["code"] == "invalid_argument"
    assert r["error"]["details"]["index"] == 1
    assert "set_selection" not in fake.calls


def test_finding_without_any_locator_is_invalid_argument():
    empty = DRCViolation(rule_id="x", severity="error", message="nothing to point at")
    r = show(EditorFake(), [courtyard("R1", 110, 60), empty])
    assert r["error"]["code"] == "invalid_argument"
    assert r["error"]["details"]["index"] == 1


def test_envelope_without_findings_is_invalid_argument():
    r = show(EditorFake(), {"summary": {"errors": 0}, "rows": [{"a": 1}]})
    assert r["error"]["code"] == "invalid_argument"


# -- resolution -------------------------------------------------------------------


def test_partial_resolution_applies_what_resolved_and_says_why_the_rest_did_not():
    finding = DRCViolation(
        rule_id="x",
        severity="error",
        message="",
        items=("R1", "R9", "R3.7"),
        nets=("OLD_NET",),
    )
    status = NetStatus(
        net_number=1,
        net_name="GND",
        connected_pads=[PadInfo("R1", "7", (0.0, 0.0), True), PadInfo("Q1", "1", (0.0, 0.0), True)],
    )
    fake = EditorFake()
    r = show(fake, [finding, status])
    assert r["ok"] is True, r
    f0, f1 = r["findings"]
    assert f0["status"] == "partial"
    assert f0["resolved"]["footprints"] == ["R1"]
    reasons = {u["locator"]["value"]: u["reason"] for u in f0["unresolved"]}
    # kct check items are untyped names; nets are typed.
    assert reasons == {"R9": "not_on_board", "R3.7": "not_on_board", "OLD_NET": "net_not_on_board"}
    reasons1 = {u["locator"]["value"]: u["reason"] for u in f1["unresolved"]}
    assert reasons1 == {"R1.7": "pad_not_on_footprint", "Q1.1": "ref_not_on_board"}
    assert r["totals"]["partial"] == 2
    assert selected(fake)["footprints"] == ["R1"]


def test_point_only_finding_selects_the_smallest_item_there():
    hit = DRCViolation(rule_id="x", severity="error", message="", location=(129.0, 65.0))
    fake = EditorFake()
    r = show(fake, [hit])
    [f] = r["findings"]
    assert f["status"] == "resolved"
    # R3's pad 1 sits inside R3's footprint box; the pad is the smaller one.
    assert f["resolved"]["pads"] == ["R3.1"]
    assert f["points"][0]["used_for"] == "resolve"


def test_empty_area_resolves_nothing_and_leaves_the_selection():
    miss = DRCViolation(rule_id="x", severity="error", message="", location=(101.0, 51.0))
    fake = EditorFake()
    fake.selected = [fake.id_of("R2")]
    r = show(fake, [miss])
    assert r["ok"] is True
    [f] = r["findings"]
    assert f["status"] == "unresolved"
    assert f["unresolved"][0]["reason"] == "empty_area"
    assert f["action"] == "nothing_to_select"
    assert r["selection_changed"] is False
    assert "set_selection" not in fake.calls
    assert selected(fake)["footprints"] == ["R2"]


def test_name_matching_a_ref_and_a_net_is_ambiguous_not_guessed():
    fake = EditorFake()
    fake.index = dict(fake.index, nets=[*fake.index["nets"], "R1"])
    names_only = DRCViolation(rule_id="x", severity="error", message="", items=("R1",))
    r = show(fake, [names_only, courtyard("R1", 110, 60)])
    f0, f1 = r["findings"]
    assert f0["unresolved"][0]["reason"] == "ambiguous_reference"
    assert f0["status"] == "unresolved"
    assert f0["resolved"]["footprints"] == []
    # With a file-frame point as well, the point resolves the finding instead.
    assert f1["unresolved"][0]["reason"] == "ambiguous_reference"
    assert f1["points"][0]["used_for"] == "resolve"
    assert f1["resolved"]["footprints"] == ["R1"]


def test_unrouted_net_without_named_pads_selects_its_pads():
    fake = EditorFake()
    r = show(fake, [NetStatus(net_number=3, net_name="SIG")])
    [f] = r["findings"]
    assert f["resolved"]["pads"] == ["R2.2", "R3.1"]
    assert "no copper" in f["net_basis"]["SIG"]


# -- mode, zoom, focus, staleness ----------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "zoom", "parts"),
    [
        ("select", False, []),
        ("highlight", False, ["highlight"]),
        ("both", True, ["highlight", "zoom"]),
        ("select", True, ["zoom"]),
    ],
)
def test_highlight_and_zoom_are_unsupported_but_selection_still_happens(mode, zoom, parts):
    fake = EditorFake()
    r = show(fake, [courtyard("R1", 110, 60)], mode=mode, zoom=zoom)
    assert r["ok"] is True
    assert [u["part"] for u in r["unsupported"]] == parts
    for u in r["unsupported"]:
        assert u["error"]["code"] == "unsupported_on_this_kicad"
    assert r["selection"]["footprints"] == ["R1"]


def test_bad_mode_is_invalid_argument():
    r = show(EditorFake(), [courtyard("R1", 110, 60)], mode="flash")
    assert r["error"]["code"] == "invalid_argument"


def test_focus_index_selects_one_finding_and_reports_the_rest():
    fs = [courtyard("R1", 110, 60), courtyard("R2", 120, 60), courtyard("R3", 130, 65)]
    fake = EditorFake()
    r = show(fake, fs, focus=1)
    assert [f["action"] for f in r["findings"]] == ["not_focused", "selected", "not_focused"]
    assert r["selection"]["footprints"] == ["R2"]
    # Every finding is still resolved and counted.
    assert r["totals"]["resolved"] == 3 and r["totals"]["focused"] == 1

    bad = show(fake, fs, focus=3)
    assert bad["error"]["code"] == "invalid_argument"


def test_focus_next_and_previous_walk_the_list():
    fs = [courtyard("R1", 110, 60), courtyard("R2", 120, 60)]
    fake = EditorFake()
    session = make_session(fake)

    def go(focus):
        r = session.run_tool(lambda s: show_findings(s, fs, focus=focus))
        assert r["ok"], r
        return r["focus"]["index"], r["focus"]["wrapped"], r["selection"]["footprints"]

    assert go("next") == (0, False, ["R1"])
    assert go("next") == (1, False, ["R2"])
    assert go("next") == (0, True, ["R1"])
    assert go("previous") == (1, True, ["R2"])
    assert go("previous") == (0, False, ["R1"])


def test_input_hash_mismatch_flags_possibly_stale_but_still_resolves():
    envelope = {"pcb_sha256": "0" * 64, "violations": [courtyard("R1", 110, 60).to_dict()]}
    fake = EditorFake()
    r = show(fake, envelope)
    assert r["ok"] is True
    assert r["staleness"]["possibly_stale"] is True
    assert r["findings"][0]["possibly_stale"] is True
    assert r["findings"][0]["resolved"]["footprints"] == ["R1"]
    assert r["totals"]["possibly_stale"] == 1


def test_input_hash_of_the_saved_file_is_not_stale():
    envelope = {
        "pcb_sha256": file_state(SELECTION_BOARD)["sha256"],
        "violations": [courtyard("R1", 110, 60).to_dict()],
    }
    r = show(EditorFake(), envelope)
    assert r["staleness"]["possibly_stale"] is False
    assert r["staleness"]["matches"] == "saved_file"


def test_no_board_open_is_no_document():
    r = show(EditorFake(open_boards=[[]]), [courtyard("R1", 110, 60)])
    assert r["error"]["code"] == "no_document"


# -- SES-10: selection -> real kct -> show_findings -> same selection ------------------


def _refs(sel):
    return set(sel["footprints"]), set(sel["pads"]), set(sel["nets"])


def _assert_round_trip(before, after):
    diff = {
        name: {"lost": sorted(b - a), "gained": sorted(a - b)}
        for name, b, a in zip(("footprints", "pads", "nets"), before, after, strict=True)
        if a != b
    }
    assert not diff, f"round trip changed the selection: {diff}"


def test_round_trip_footprints_through_kct_check():
    fake = EditorFake()
    fake.selected = [fake.id_of("R1"), fake.id_of("R2")]
    sel = selected(fake)
    wanted = set(sel["footprints"])

    report = kct_json("check", SELECTION_BOARD, "--format", "json")
    assert report["pcb_sha256"] == file_state(SELECTION_BOARD)["sha256"]
    # "check these parts": keep the findings whose items are all selected refs.
    report["violations"] = [v for v in report["violations"] if set(v["items"]) <= wanted]
    assert report["violations"], "kct check reported nothing on the selected footprints"

    r = show(fake, report)
    assert r["ok"] is True, r
    assert r["staleness"]["possibly_stale"] is False
    _assert_round_trip(_refs(sel), _refs(selected(fake)))

    # Locations agree within 1 um in the file frame.
    geo = make_session(fake).run_tool(lambda s: get_selection(s, include_geometry=True))
    for v in report["violations"]:
        [ref] = v["items"]
        pos = geo["geometry"][ref]["position_mm"]
        assert abs(pos["x_mm"] - v["location"][0]) <= 1e-3
        assert abs(pos["y_mm"] - v["location"][1]) <= 1e-3


def test_round_trip_pads_and_net_through_kct_net_status():
    fake = EditorFake()
    fake.selected = [fake.id_of("R1.1"), fake.id_of("R2.1")]
    sel = selected(fake)
    assert sel["nets"] == ["VIN"]

    report = kct_json("net-status", SELECTION_BOARD, "--format", "json", "--net", sel["nets"][0])
    r = show(fake, report)
    assert r["ok"] is True, r
    after = selected(fake)
    _assert_round_trip(_refs(sel), _refs(after))

    # net-status positions are board-frame; converted, they land on the pads (1 um).
    geo = make_session(fake).run_tool(lambda s: get_selection(s, include_geometry=True))
    points = r["findings"][0]["points"]
    assert all(p["within_resolved_items"] for p in points)
    for name in sel["pads"]:
        pos = geo["geometry"][name]["position_mm"]
        assert any(
            abs(p["file_mm"]["x_mm"] - pos["x_mm"]) <= 1e-3
            and abs(p["file_mm"]["y_mm"] - pos["y_mm"]) <= 1e-3
            for p in points
        ), f"no net-status point within 1 um of {name}"


def test_round_trip_pads_through_kct_check_connectivity():
    fake = EditorFake()
    fake.selected = [fake.id_of("R3.1"), fake.id_of("R3.2")]
    sel = selected(fake)
    wanted = set(sel["pads"])

    report = kct_json("check", SELECTION_BOARD, "--format", "json")
    report["violations"] = [
        v for v in report["violations"] if v["items"] and set(v["items"]) <= wanted
    ]
    assert len(report["violations"]) == 2

    r = show(fake, report)
    assert r["ok"] is True, r
    _assert_round_trip(_refs(sel), _refs(selected(fake)))
