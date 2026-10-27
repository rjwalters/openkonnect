"""Command line: ``openkonnect serve`` (MCP over stdio) and ``openkonnect status``."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .session import Session, SessionConfig


def _add_connection_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="hard bound on each tool call, in seconds (env OPENKONNECT_TIMEOUT, default 5)",
    )
    p.add_argument(
        "--request-timeout",
        type=float,
        default=None,
        help="bound on one IPC request, in seconds (env OPENKONNECT_REQUEST_TIMEOUT, default 2)",
    )
    p.add_argument(
        "--busy-retries",
        type=int,
        default=None,
        help="retries when KiCad answers busy (env OPENKONNECT_BUSY_RETRIES, default 3)",
    )
    p.add_argument(
        "--socket",
        default=None,
        help="KiCad API socket URL, e.g. ipc:///tmp/kicad/api.sock (env OPENKONNECT_SOCKET; "
        "default: discover)",
    )


def _session(args: argparse.Namespace) -> Session:
    return Session(
        SessionConfig.from_env(
            timeout_s=args.timeout,
            request_timeout_s=args.request_timeout,
            busy_retries=args.busy_retries,
            socket=args.socket,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="openkonnect", description=__doc__)
    parser.add_argument("--version", action="version", version=f"openkonnect {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the MCP server on stdio")
    _add_connection_args(serve)
    status = sub.add_parser("status", help="print session_status as JSON and exit")
    _add_connection_args(status)

    args = parser.parse_args(argv)
    try:
        session = _session(args)
    except ValueError as e:
        parser.error(str(e))

    if args.command == "status":
        from .tools.session_status import session_status

        result = session.run_tool(session_status)
        json.dump(result, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0 if result["ok"] else 1

    from .server import build_server

    build_server(session).run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
