# IPC client decision

**Decision (issue #2, 2026-10-27):** openkonnect talks to KiCad 10 through
**kicad-python** (import name `kipy`), KiCad's official Python bindings for
its IPC API.

| | |
|---|---|
| Package | [`kicad-python`](https://pypi.org/project/kicad-python/) |
| Version checked | 0.8.0 (sdist `kicad_python-0.8.0.tar.gz`, sha256 `6d51b9a3098f3196927f9ce124adc160450862cf287dc3994dec4de4f968e0fa`) |
| Built against | KiCad API `10.0.6-0-gcaf7377e9c` (`kipy.kicad_api_version`) |
| License | **MIT**, "Copyright The KiCad Developers" |
| How verified | The sdist's `LICENSE` file (MIT text, KiCad Developers copyright), its `PKG-INFO` (`License-Expression: MIT`), and the MIT header in each module, e.g. `kipy/client.py` |
| Repository | <https://gitlab.com/kicad/code/kicad-python> |
| Dependency range | `kicad-python>=0.8,<0.9` in `pyproject.toml` |

kicad-python is used as a **dependency**. No code from it is copied into this
repository, so `NOTICE` gets no entry. If code is ever copied, add an entry
there and keep the file's MIT header (CLEANROOM.md, "Permitted third-party
code").

## Why

- It is KiCad's own client, published by the KiCad project. It tracks the
  API's protobuf messages as KiCad changes them, so openkonnect does not carry
  its own copy of the protocol.
- It is MIT, the same license as openkonnect.
- It covers everything Phase 0 needs: ping, version, open documents, the
  board's items, enabled layers, title block and bounding boxes. Its errors
  (`kipy.errors.ConnectionError`, and `ApiError` with KiCad's status codes such
  as `AS_BUSY` and `AS_TOKEN_MISMATCH`) map directly onto the spec's C-3 codes.
- It talks only over IPC. It does not use the `pcbnew` SWIG binding or GUI
  automation.

## How it is used

Only `src/openkonnect/backend.py` imports `kipy`. It wraps it in a small
`IpcBackend` interface, and the session layer (`session.py`) maps kipy's
exceptions to C-3 errors. The tests replace the backend with a fake that
raises the real kipy exception types. The backend reads two private kipy
attributes (`KiCad._client`, `KiCadClient._kicad_token`) for the instance token and for
closing the connection. Both reads are defensive and should be rechecked when
the version range is raised.

## Rejected alternatives

1. **`kicad_tools.ipc`** (kicad-tools, MIT, `pynng` extra). It keeps its own
   copy of the proto messages, which can lag the API, and it would tie the
   live layer to kicad-tools' release cadence. kicad-tools is still a
   dependency for file-level work; `kct ipc status` gives a cross-check.
2. **KiCAD-MCP-Server's IPC backend** (MIT, IPC parts only). Copying it would
   bring attribution work for little gain over the official client. Reuse it
   only for something kipy lacks, and record it in `NOTICE` if we do.

## Black-box facts used

From KiCad 10.0.7 on macOS, observed without reading KiCad source:

- The preference that turns the API on is labelled **"Enable KiCad API"**, on
  the Plugins page of KiCad's preferences. It is stored as `api.enable_server`
  in `<KiCad config dir>/10.0/kicad_common.json`. openkonnect reads that value
  only as a hint when choosing between `ipc_disabled` and `ipc_unreachable`.
- KiCad GUI process names are `kicad`, `pcbnew` and `eeschema`.
- The default socket is `/tmp/kicad/api.sock` (from kicad-python's default and
  KiCad's public docs). When that name is taken, KiCad appends the process ID.
