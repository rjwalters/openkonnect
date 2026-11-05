"""LAY-04 ``list_nets`` against a fake PCB editor."""

from __future__ import annotations

import re
from pathlib import Path

from openkonnect.tools.list_nets import list_nets, version_tuple

from .conftest import SELECTION_BOARD, EditorFake, FakeBackend, make_session


def run(backend, **kw):
    return make_session(backend).run_tool(lambda s: list_nets(s, **kw))


def by_name(r):
    return {n["name"]: n for n in r["nets"]}


def fixture_pad_counts() -> dict[str, int]:
    """Pads per net, counted from the fixture file's ``(pad ...)`` blocks."""
    text = Path(SELECTION_BOARD).read_text(encoding="utf-8")
    counts: dict[str, int] = {}
    for block in re.split(r"\n\t\t\(pad ", text)[1:]:
        m = re.search(r'\(net \d+ "([^"]+)"\)', block)
        if m:
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def test_pad_counts_match_the_fixture_file():
    # Read off tests/fixtures/selection.kicad_pcb: R1.1 R2.1 on VIN, R1.2 R3.2 on GND,
    # R2.2 R3.1 on SIG. The fixture's three segments are copper, not pads.
    expected = {"GND": 2, "SIG": 2, "VIN": 2}
    assert fixture_pad_counts() == expected
    r = run(EditorFake())
    assert r["ok"] is True, r
    assert {n["name"]: n["pad_count"] for n in r["nets"]} == expected


def test_unfiltered_list_has_classes_and_skips_the_unnamed_net():
    fake = EditorFake()
    fake.index["nets"] = ["", *fake.index["nets"]]
    r = run(fake)
    assert r["count"] == 3
    assert [n["name"] for n in r["nets"]] == ["GND", "SIG", "VIN"]
    assert {n["name"]: n["net_class"] for n in r["nets"]} == {
        "GND": "Default",
        "SIG": "Default",
        "VIN": "Power",
    }
    assert "filters" not in r
    assert "pads" not in r["nets"][0]
    assert fake.calls.count("kicad_version") == 0


def test_pattern_filter_is_a_case_sensitive_glob():
    assert [n["name"] for n in run(EditorFake(), pattern="V*")["nets"]] == ["VIN"]
    assert [n["name"] for n in run(EditorFake(), pattern="?ND")["nets"]] == ["GND"]
    assert run(EditorFake(), pattern="vin")["nets"] == []
    r = run(EditorFake(), pattern="NOPE*")
    assert r["ok"] is True and r["count"] == 0 and r["nets"] == []
    assert r["filters"] == {"pattern": "NOPE*"}


def test_class_filter():
    r = run(EditorFake(), net_class="Power")
    assert [n["name"] for n in r["nets"]] == ["VIN"]
    assert [n["name"] for n in run(EditorFake(), net_class="Default")["nets"]] == ["GND", "SIG"]
    r = run(EditorFake(), net_class="Unknown")
    assert r["ok"] is True and r["nets"] == []


def test_filters_combine():
    assert run(EditorFake(), pattern="G*", net_class="Power")["nets"] == []
    assert [n["name"] for n in run(EditorFake(), pattern="G*", net_class="Default")["nets"]] == [
        "GND"
    ]


def test_include_items_lists_pads_and_copper_by_reference():
    r = run(EditorFake(), include_items=True)
    assert r["ok"] is True, r
    nets = by_name(r)
    assert nets["VIN"]["pads"] == ["R1.1", "R2.1"]
    assert nets["GND"]["pads"] == ["R1.2", "R3.2"]
    assert nets["SIG"]["pads"] == ["R2.2", "R3.1"]
    assert nets["GND"]["other"] == []
    assert [o["id"] for o in nets["VIN"]["other"]] == ["trk-1", "trk-2", "trk-3"]
    assert nets["VIN"]["other"][0] == {
        "kind": "track",
        "id": "trk-1",
        "id_scope": "session",
        "no_file_reference": True,
    }


def test_net_with_zero_pads():
    fake = EditorFake()
    fake.index["nets"].append("NC")
    n = by_name(run(fake, include_items=True))["NC"]
    assert n["pad_count"] == 0 and n["pads"] == [] and n["other"] == []


def test_include_items_refused_on_10_0_0_before_any_read():
    fake = EditorFake(kicad_version="10.0.0")
    r = run(fake, include_items=True)
    assert r["ok"] is False
    assert r["error"]["code"] == "unsupported_on_this_kicad"
    assert r["error"]["details"]["min_kicad"] == "10.0.1"
    assert r["error"]["details"]["flag"] == "include_items"
    assert "nets" not in fake.calls


def test_plain_list_still_works_on_10_0_0():
    r = run(EditorFake(kicad_version="10.0.0"))
    assert r["ok"] is True and r["count"] == 3


def test_include_items_allowed_on_10_0_1():
    assert run(EditorFake(kicad_version="10.0.1"), include_items=True)["ok"] is True


def test_no_document_when_no_board_is_open():
    r = run(FakeBackend())
    assert r["ok"] is False
    assert r["error"]["code"] == "no_document"


def test_bad_argument_types():
    r = run(EditorFake(), pattern=5)
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


def test_version_tuple():
    assert version_tuple("10.0.1") == (10, 0, 1)
    assert version_tuple("10.0.1-rc1 (x86_64)") == (10, 0, 1)
    assert version_tuple("10.1") == (10, 1, 0)
    assert version_tuple("9.0.7") < (10, 0, 1)
    assert version_tuple("") is None and version_tuple(None) is None
