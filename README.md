# openkonnect

An MIT-licensed MCP server that lets an LLM agent drive a **running KiCad 10**:
schematic, layout, placement, routing, design review and manufacturing tools,
through KiCad's IPC API.

> **Status: not yet started.** This repository holds the project charter and the
> clean-room rules. No implementation has landed. See [ROADMAP.md](ROADMAP.md)
> for the plan.

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
- **KiCad 10 IPC API** for the live editor. No `pcbnew` SWIG bindings.
- **MCP** as the agent interface, usable from any MCP client.

## License

MIT. See [LICENSE](LICENSE).
