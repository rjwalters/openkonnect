# openkonnect specifications

This directory holds the clean-room specifications that openkonnect is built
from. Under [CLEANROOM.md](../CLEANROOM.md), a capability gets built only after
a spec author has described it here in our own words. Implementers then work
from this spec, KiCad's public API docs and permissively licensed code. The
spec author and the implementer of a capability are never the same person.

| File | What it is |
|------|------------|
| [capabilities.md](capabilities.md) | Capability spec v0 (#1): every tool the server should offer, grouped by area, with each one classified as file-level (delegated to kicad-tools) or live-editor (owned here), plus provenance for every source used |

## Rules for changing a spec

- Keep the spec author role separate. Whoever edits an entry's purpose, inputs,
  outputs or failure modes must not implement that entry, and the PR must say so.
- Write in your own words. Do not paste tool descriptions, schemas or prompt
  text from any other project.
- Add every new source to the provenance section of `capabilities.md`, with its
  URL and the date you accessed it.
- Do not put implementation guidance in a spec: no class names, library calls
  or algorithms. Say what the tool must do, not how.
