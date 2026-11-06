"""LAY-03 ``footprint_pads`` against a fake PCB editor."""

from __future__ import annotations

import re
from pathlib import Path

from openkonnect.tools.footprint_pads import candidate_refs, footprint_pads

from .conftest import SELECTION_BOARD, EditorFake, FakeBackend, make_session


def run(backend, refs):
    return make_session(backend).run_tool(lambda s: footprint_pads(s, refs))


def fixture_pad_nets() -> dict[str, str]:
    """``REF.PAD`` -> net name, read from the fixture file's footprint and pad blocks."""
    text = Path(SELECTION_BOARD).read_text(encoding="utf-8")
    nets: dict[str, str] = {}
    for fp in re.split(r"\n\t\(footprint ", text)[1:]:
        ref = re.search(r'\(property "Reference" "([^"]+)"', fp).group(1)
        for block in re.split(r"\n\t\t\(pad ", fp)[1:]:
            num = re.match(r'"([^"]+)"', block).group(1)
            nets[f"{ref}.{num}"] = re.search(r'\(net \d+ "([^"]+)"\)', block).group(1)
    return nets


def test_nets_match_the_fixture_file_literals():
    expected = {
        "R1.1": "VIN",
        "R1.2": "GND",
        "R2.1": "VIN",
        "R2.2": "SIG",
        "R3.1": "SIG",
        "R3.2": "GND",
    }
    assert fixture_pad_nets() == expected
    r = run(EditorFake(), ["R1", "R2", "R3"])
    assert r["ok"] is True, r
    assert {p["name"]: p["net"] for p in r["pads"]} == expected
    for p in r["pads"]:
        assert p["layers"] == ["F.Cu", "F.Mask", "F.Paste"]
        assert p["net_state"] == "connected"


def test_one_footprints_pads():
    r = run(EditorFake(), ["R1"])
    assert r["count"] == 2
    p1, p2 = r["pads"]
    assert (p1["name"], p1["number"], p1["ref"]) == ("R1.1", "1", "R1")
    assert p1["position_mm"] == {"frame": "file", "x_mm": 109.0, "y_mm": 60.0}
    assert p1["position_board_mm"] == {"frame": "board", "x_mm": 9.0, "y_mm": 10.0}
    assert p2["position_mm"]["x_mm"] == 111.0
    assert p1["shape"] == "rectangle"
    assert p1["size_mm"] == {"x_mm": 1.0, "y_mm": 1.2}
    assert r["frame_offset"]["present"] is True
    assert r["frame_offset"]["x_mm"] == 100.0 and r["frame_offset"]["y_mm"] == 50.0


def test_single_ref_pad():
    r = run(EditorFake(), ["R2.2"])
    assert [p["name"] for p in r["pads"]] == ["R2.2"]
    assert r["pads"][0]["net"] == "SIG"


def test_mix_of_ref_and_ref_pad_is_deduplicated():
    r = run(EditorFake(), ["R3.2", "R1", "R1.2"])
    assert [p["name"] for p in r["pads"]] == ["R1.1", "R1.2", "R3.2"]


def test_unknown_ref_is_not_found():
    r = run(EditorFake(), ["R1", "C9"])
    assert r["ok"] is False
    assert r["error"]["code"] == "not_found"
    assert r["error"]["details"]["not_found"] == ["C9"]
    assert r["error"]["details"]["found"] == ["R1.1", "R1.2"]


def test_unknown_pad_on_known_ref_is_not_found():
    for entry in ("R1.9", "R1.", "R1.1.1"):
        r = run(EditorFake(), [entry])
        assert r["error"]["code"] == "not_found", entry
        assert r["error"]["details"]["not_found"] == [entry]


def test_unreadable_net_is_null_with_a_reason_not_unconnected():
    fake = EditorFake()
    fake.unreadable_nets = {"R1.1"}
    r = run(fake, ["R1"])
    p1, p2 = r["pads"]
    assert p1["net"] is None and p1["net_state"] == "unknown"
    assert "net_reason" in p1
    assert p2["net"] == "GND" and "net_reason" not in p2


def test_unconnected_pad_is_distinct_from_unknown():
    fake = EditorFake()
    fake.index["footprints"][0]["pads"][0]["net"] = None
    r = run(fake, ["R1.1"])
    assert r["pads"][0]["net"] is None
    assert r["pads"][0]["net_state"] == "unconnected"


def test_board_frame_is_omitted_without_an_outline():
    fake = EditorFake()
    fake.index["outline_nm"] = None
    r = run(fake, ["R1.1"])
    assert "position_board_mm" not in r["pads"][0]
    assert r["frame_offset"]["present"] is False


def test_refs_must_be_a_non_empty_list_of_strings():
    for bad in (None, [], [1], [""], 5):
        r = run(EditorFake(), bad)
        assert r["error"]["code"] == "invalid_argument", bad


def test_no_document_when_no_board_is_open():
    r = run(FakeBackend(open_boards=[[]]), ["R1"])
    assert r["ok"] is False
    assert r["error"]["code"] == "no_document"


def test_candidate_refs_cover_every_dot_split():
    assert candidate_refs(["R1.2", "U.1.3"]) == ["R1", "R1.2", "U", "U.1", "U.1.3"]
