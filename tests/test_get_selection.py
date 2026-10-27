"""SES-08 ``get_selection`` against a fake PCB editor, plus the kipy decoding path."""

from __future__ import annotations

from google.protobuf.any_pb2 import Any as AnyProto
from kipy.proto.board import board_types_pb2
from kipy.proto.common.commands import editor_commands_pb2 as ed
from kipy.proto.common.types import base_types_pb2

from openkonnect.backend import _boxes, _describe, _kind_of, _read_selection
from openkonnect.tools.get_selection import get_selection

from .conftest import EditorFake, FakeBackend, make_session


def run(backend, **kw):
    return make_session(backend).run_tool(lambda s: get_selection(s, **kw))


def test_empty_selection_is_an_ordinary_result():
    r = run(EditorFake())
    assert r["ok"] is True
    assert r["empty"] is True
    assert r["count"] == 0
    assert r["footprints"] == r["pads"] == r["nets"] == r["other"] == []
    assert r["undescribed"] == {"count": 0, "by_type": {}}
    assert r["bbox"] is None
    assert r["frame_offset"]["x_mm"] == 100.0 and r["frame_offset"]["y_mm"] == 50.0


def test_mixed_selection_gives_file_level_references():
    fake = EditorFake()
    fake.selected = [fake.id_of("R1"), fake.id_of("R2.1"), "trk-2"]
    r = run(fake)
    assert r["ok"] is True, r
    assert r["footprints"] == ["R1"]
    assert r["pads"] == ["R2.1"]
    # Nets of the selected pad and track; footprint nets are listed separately.
    assert r["nets"] == ["VIN"]
    assert r["nets_of_footprints"] == ["GND", "VIN"]
    [track] = r["other"]
    assert track["kind"] == "track"
    assert track["net"] == "VIN"
    assert track["layer"] == "F.Cu"
    assert track["id"] == "trk-2"
    assert track["id_scope"] == "session"
    assert track["no_file_reference"] is True
    # Union of R1 (108.5..111.5, 59.4..60.6), pad R2.1 and the y=57 track.
    b = r["bbox"]
    assert b["frame"] == "file"
    assert (b["x0_mm"], b["y0_mm"]) == (108.5, 56.875)
    assert (b["x1_mm"], b["y1_mm"]) == (119.5, 60.6)
    assert r["count"] == 3
    assert "geometry" not in r


def test_frame_offset_lets_callers_convert_to_board_frame():
    fake = EditorFake()
    fake.selected = [fake.id_of("R1")]
    r = run(fake, include_geometry=True)
    off = r["frame_offset"]
    assert off["present"] is True
    pos = r["geometry"]["R1"]["position_mm"]
    assert pos == {"frame": "file", "x_mm": 110.0, "y_mm": 60.0}
    # board = file - offset: R1 sits 10 mm right of and 10 mm below the outline corner.
    assert (pos["x_mm"] - off["x_mm"], pos["y_mm"] - off["y_mm"]) == (10.0, 10.0)


def test_no_outline_means_no_board_frame():
    fake = EditorFake()
    fake.index = dict(fake.index, outline_nm=None)
    r = run(fake)
    assert r["frame_offset"]["present"] is False


def test_undescribed_items_are_counted_not_dropped():
    fake = EditorFake(undescribed={"kiapi.board.types.Generator": 2})
    fake.selected = [fake.id_of("R3")]
    r = run(fake)
    assert r["ok"] is True
    assert r["empty"] is False
    assert r["undescribed"] == {"count": 2, "by_type": {"kiapi.board.types.Generator": 2}}
    assert r["count"] == 3
    assert r["footprints"] == ["R3"]


def test_kind_filter_is_passed_through_and_validated():
    fake = EditorFake()
    fake.selected = [fake.id_of("R1"), "trk-1"]
    r = run(fake, kinds=["track"])
    assert r["ok"] is True
    assert r["kinds"] == ["track"]
    assert r["footprints"] == []
    assert [o["kind"] for o in r["other"]] == ["track"]

    bad = run(EditorFake(), kinds=["footprint", "teapot"])
    assert bad["ok"] is False
    assert bad["error"]["code"] == "invalid_argument"
    assert bad["error"]["details"]["unknown"] == ["teapot"]


def test_no_board_open_is_no_document():
    r = run(EditorFake(open_boards=[[]]))
    assert r["ok"] is False
    assert r["error"]["code"] == "no_document"


def test_unreachable_never_reads_the_selection():
    backend = FakeBackend()
    r = make_session(backend, sockets=[], running=False).run_tool(get_selection)
    assert r["ok"] is False
    assert r["error"]["code"] == "kicad_not_running"
    assert "selection" not in backend.calls


# -- the kipy side: real protobuf messages through the backend's decoder ----------


class ProtoClient:
    """Stands in for kipy's KiCadClient: answers one request type with a canned reply."""

    def __init__(self, replies):
        self.replies = replies
        self.sent = []

    def send(self, command, response_type):
        self.sent.append(command)
        return self.replies[response_type]


def _packed(msg):
    a = AnyProto()
    a.Pack(msg)
    return a


def test_read_selection_counts_items_kipy_cannot_decode():
    track = board_types_pb2.Track()
    track.id.value = "trk-a"
    track.layer = board_types_pb2.BoardLayer.BL_F_Cu
    track.net.name = "VIN"
    fp = board_types_pb2.FootprintInstance()
    fp.id.value = "fp-a"
    fp.reference_field.text.text.text = "U7"
    # A message type kipy has no wrapper for, as a newer KiCad might send.
    odd = base_types_pb2.KIID(value="x")
    reply = ed.SelectionResponse(items=[_packed(track), _packed(fp), _packed(odd)])
    client = ProtoClient({ed.SelectionResponse: reply})
    doc = base_types_pb2.DocumentSpecifier()

    items, undescribed = _read_selection(client, doc, [])
    assert [_kind_of(i) for i in items] == ["track", "footprint"]
    assert undescribed == {"kiapi.common.types.KIID": 1}
    assert isinstance(client.sent[0], ed.GetSelection)

    t = _describe(items[0], "track", {})
    assert t == {"id": "trk-a", "kind": "track", "net": "VIN", "layer": "F.Cu"}
    f = _describe(items[1], "footprint", {})
    assert f["ref"] == "U7"


def test_boxes_keep_the_item_to_box_pairing():
    track = board_types_pb2.Track()
    track.id.value = "trk-a"
    via = board_types_pb2.Via()
    via.id.value = "via-b"
    from kipy.board_types import Track, Via

    resp = ed.GetBoundingBoxResponse()
    # KiCad answers only for items that have a box; here the via only.
    resp.items.add(value="via-b")
    box = resp.boxes.add()
    box.position.x_nm, box.position.y_nm = 1_000, 2_000
    box.size.x_nm, box.size.y_nm = 300, 400
    client = ProtoClient({ed.GetBoundingBoxResponse: resp})
    out = _boxes(client, base_types_pb2.DocumentSpecifier(), [Track(track), Via(via)])
    assert out == {"via-b": {"x0_nm": 1_000, "y0_nm": 2_000, "x1_nm": 1_300, "y1_nm": 2_400}}


def test_kipy_backend_maps_every_selection_kind():
    from openkonnect.backend import SELECTION_KINDS, _kind_types

    assert set(_kind_types()) == set(SELECTION_KINDS)
