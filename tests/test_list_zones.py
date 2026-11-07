"""LAY-06 ``list_zones`` against a fake PCB editor."""

from __future__ import annotations

from openkonnect.tools.list_zones import list_zones

from .conftest import EditorFake, FakeBackend, make_session


def run(backend, **kw):
    return make_session(backend).run_tool(lambda s: list_zones(s, **kw))


def by_id(r):
    return {z["id"]: z for z in r["zones"]}


def test_copper_zone_and_rule_area():
    r = run(EditorFake())
    assert r["ok"] is True, r
    assert r["count"] == 2 and "filters" not in r
    z = by_id(r)
    cu = z["zone-1"]
    assert cu["kind"] == "copper" and cu["net"] == "GND" and cu["name"] == "GND pour"
    assert cu["layers"] == ["F.Cu", "B.Cu"] and cu["priority"] == 1
    assert cu["pad_connection"] == "thermal" and cu["filled"] is True
    assert cu["keepouts"] is None  # edge: copper zone has no keepouts
    assert cu["id_scope"] == "session" and cu["no_file_reference"] is True
    ra = z["zone-2"]
    assert ra["kind"] == "rule_area" and ra["locked"] is True
    assert ra["net"] is None and ra["pad_connection"] is None and ra["name"] is None
    assert ra["keepouts"] == {
        "copper": True,
        "vias": True,
        "tracks": False,
        "pads": False,
        "footprints": False,
    }


def test_net_filter():
    r = run(EditorFake(), net="GND")
    assert [z["id"] for z in r["zones"]] == ["zone-1"]
    assert r["filters"] == {"net": "GND"}


def test_layer_filter():
    assert [z["id"] for z in run(EditorFake(), layer="B.Cu")["zones"]] == ["zone-1"]
    assert run(EditorFake(), layer="F.Cu")["count"] == 2


def test_empty_match_is_not_an_error():
    r = run(EditorFake(), net="NOPE")
    assert r["ok"] is True and r["count"] == 0 and r["zones"] == []
    assert run(EditorFake(), layer="In1.Cu")["zones"] == []


def test_filters_combine():
    assert run(EditorFake(), net="GND", layer="F.Cu")["count"] == 1


def test_unknown_layer_is_invalid_argument():
    fake = EditorFake()
    r = run(fake, layer="Nope.Cu")
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"
    assert "zones" not in fake.calls


def test_non_string_filter_is_invalid_argument():
    r = run(EditorFake(), net=5)
    assert r["ok"] is False and r["error"]["code"] == "invalid_argument"


def test_no_document():
    r = run(FakeBackend(open_boards=[[]]))
    assert r["ok"] is False and r["error"]["code"] == "no_document"
