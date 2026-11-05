"""SES-02 ``list_instances`` against fake backends."""

from __future__ import annotations

import time

from openkonnect.tools.list_instances import list_instances

from .conftest import FakeBackend, board_ref, make_session, refused

A = "ipc:///tmp/kicad/api.sock"
B = "ipc:///tmp/kicad/api-4242.sock"


def test_answering_instance_is_reported():
    ref = board_ref("/proj", "demo")
    be = FakeBackend(open_boards=[[ref]])
    r = list_instances(make_session(be, sockets=[A]))
    assert r["ok"] is True and r["listable"] is True
    (inst,) = r["instances"]
    assert inst["endpoint"] == A
    assert inst["pid"] is None
    assert inst["answered"] is True
    assert inst["kicad_version"] == "10.0.7"
    assert inst["board_path"] == ref.path
    assert inst["reason"] is None
    assert be.closed


def test_pid_from_socket_name_and_unanswered_reason():
    def factory(sock, ms):
        return FakeBackend(sock) if sock == A else FakeBackend(sock, ping=refused())

    r = list_instances(make_session(factory, sockets=[A, B]))
    ok, bad = r["instances"]
    assert ok["answered"] is True
    assert bad["pid"] == 4242
    assert bad["answered"] is False
    assert bad["reason"]["code"] == "ipc_unreachable"


def test_no_sockets_is_empty_list_not_error():
    r = list_instances(make_session(sockets=[]))
    assert r["ok"] is True and r["listable"] is True and r["instances"] == []


def test_unlistable_is_distinct_from_empty():
    r = list_instances(make_session(sockets=None))
    assert r["ok"] is True
    assert r["listable"] is False
    assert r["instances"] == []


def test_hanging_probe_is_bounded_per_socket():
    be = FakeBackend(ping=lambda: time.sleep(5))
    start = time.monotonic()
    r = list_instances(make_session(be, sockets=[A], timeout_s=0.2))
    assert time.monotonic() - start < 1.5
    (inst,) = r["instances"]
    assert inst["answered"] is False
    assert inst["reason"]["code"] == "ipc_unreachable"


def test_registered_as_server_tool():
    import anyio
    from mcp.shared.memory import create_connected_server_and_client_session

    from openkonnect.server import build_server

    async def go():
        server = build_server(make_session(FakeBackend(), sockets=[A]))
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            return await client.call_tool("list_instances", {})

    res = anyio.run(go)
    assert res.isError is False
    assert res.structuredContent["instances"][0]["answered"] is True
