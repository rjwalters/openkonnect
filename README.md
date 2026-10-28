# openkonnect

An MIT-licensed MCP server that lets an LLM agent drive a **running KiCad 10**:
schematic, layout, placement, routing, design review and manufacturing tools,
through KiCad's IPC API.

> **Status: Phase 0 bootstrap.** The MCP server runs and exposes read-only
> tools that inspect the session and the open board, plus the save, sync and
> handoff tools that write files or reload the editor (see [Tools](#tools)).
> See [ROADMAP.md](ROADMAP.md) for the plan and
> [spec/capabilities.md](spec/capabilities.md) for what each tool does.

## Quickstart

**1. Install.** You need [uv](https://docs.astral.sh/uv/) and Python 3.10 or
newer.

```bash
git clone https://github.com/rjwalters/openkonnect
cd openkonnect
uv sync
uv run openkonnect --version
```

**2. Turn on KiCad's API server.** In KiCad 10, open *Preferences > Plugins*,
check **Enable KiCad API**, and restart KiCad. The API is off by default.
Then open your board in the PCB editor. Check the connection with:

```bash
uv run openkonnect status
```

It prints `session_status` as JSON. `"ipc_reachable": true` means the server
can reach KiCad. Otherwise `reason` says what is wrong, for example
`kicad_not_running` or `ipc_disabled`.

**3. Register the server with your MCP client.** The server speaks MCP over
stdio. For Claude Code, run:

```bash
claude mcp add openkonnect -- uv run --directory /path/to/openkonnect openkonnect serve
```

For clients configured with JSON (Claude Desktop, Cursor and similar):

```json
{
  "mcpServers": {
    "openkonnect": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/openkonnect", "openkonnect", "serve"],
      "env": { "OPENKONNECT_TIMEOUT": "5" }
    }
  }
}
```

### Tools

| Tool | Spec | What it returns |
|------|------|-----------------|
| `session_status` | SES-01 | Whether KiCad is running and its API reachable, versions, instance, project directory, open documents with dirty state, and which tools this instance supports. "Not running" and "API disabled" come back as data, not errors. |
| `board_summary` | LAY-01 | Live overview of the open board, unsaved changes included: path, title block, layer counts, counts of footprints, nets, tracks, vias and zones, outline size, dirty state. |
| `get_selection` | SES-08 | What is selected in the PCB editor, as file-level references `kct` accepts (reference designators, `REF.PAD`, nets), plus other items with session-scoped ids and the selection's bounding box. An empty selection is an ordinary result. |
| `show_findings` | SES-09 | Selects in the editor the items that kicad-tools findings (`kct ... --format json`) point at, and reports per finding what resolved, what did not and why, and which findings may be stale. |
| `save_before_external_write` | SES-04 | Before a `kct` command touches the open board: each document's state before, the action taken (none, saved or refused), and the file and editor hashes to pass to `sync_from_disk`. Refuses a dirty or unknown board unless `allow_save`. See [the handoff protocol](docs/handoff-protocol.md). |
| `sync_from_disk` | SES-05 | After a `kct` command wrote the board: per document, whether the editor was reloaded from the file and whether unsaved changes were discarded. Refuses, changing nothing, when the editor may hold unsaved work, unless `discard_unsaved`. See [the handoff protocol](docs/handoff-protocol.md). |
| `run_kct_handoff` | SES-06 | Runs one allow-listed `kct` write command on the open board through the whole protocol (check, save, run, reload only on success) and reports each step and the command's result. See [the handoff protocol](docs/handoff-protocol.md). |

On KiCad 10, `dirty` is always `"unknown"` with `dirty_basis: "none"`: KiCad
10's API cannot report unsaved changes. Treat `unknown` as dirty.

Every failure is a structured error (`code`, `message`, `retryable`,
`details`), and every result carries `meta` with the time bound used and the
number of busy retries.

| Setting | Flag | Environment | Default |
|---------|------|-------------|---------|
| Bound on one tool call (s) | `--timeout` | `OPENKONNECT_TIMEOUT` | 5 |
| Bound on one IPC request (s) | `--request-timeout` | `OPENKONNECT_REQUEST_TIMEOUT` | 2 |
| Retries when KiCad is busy | `--busy-retries` | `OPENKONNECT_BUSY_RETRIES` | 3 |
| API socket | `--socket` | `OPENKONNECT_SOCKET` | discovered |

### Development

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -rs
```

Unit tests run against a fake IPC backend. Tests marked `live_kicad` need a
running KiCad 10 with the API on and `tests/fixtures/board.kicad_pcb` open,
and skip otherwise (always in CI): `uv run pytest -m live_kicad -rs`.

## Why this exists

In October 2026 the open-source AGPL KiCad agent plugin *Konnect* was archived,
and development moved to a commercial, closed-source successor. openkonnect is
an independent, **clean-room** implementation of the same kind of tool, under
a permissive license, so that a free live-editor agent interface for KiCad
stays available.

**openkonnect is not affiliated with, endorsed by, or derived from Konnect,
Konnect-EDA or Orchis.ai LLC.** It contains none of their code. See
[CLEANROOM.md](CLEANROOM.md) for how that is kept true.

## Design

- **Python**, built on [kicad-tools](https://github.com/rjwalters/kicad-tools)
  (MIT). File-level work (parsing, checks, netlists, exports) comes from
  kicad-tools. openkonnect adds what needs a live editor: the IPC session,
  selection and view state, and interactive edits in an open board or schematic.
- **KiCad 10 IPC API** for the live editor, through KiCad's official
  `kicad-python` bindings (MIT; see [docs/ipc-client.md](docs/ipc-client.md)).
  No `pcbnew` SWIG bindings.
- **MCP** as the agent interface, usable from any MCP client.

## License

MIT. See [LICENSE](LICENSE). Third-party code reused with attribution is
listed in [NOTICE](NOTICE).
