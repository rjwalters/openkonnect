# Clean-room rules

openkonnect re-implements the *capabilities* of an AGPL-3.0 project
(mixelpixx/Konnect, archived 2026-10-26) under the MIT license. That is only
legitimate if no protected expression from that project reaches this
repository. These rules are binding on every contributor, human or agent.

## Two roles

1. **Specification authors** may study the original project's *public
   behaviour*: its README, published documentation, tool names and their
   documented inputs and outputs, and black-box runs of released binaries. They
   write a capability spec in their own words: what a tool does, what it takes
   and what it returns. They never write implementation guidance taken from
   source code.
2. **Implementers** work only from the spec in this repo, from KiCad's own public
   API documentation, and from MIT-, BSD-, ISC- or Apache-2.0-licensed code
   (for example kicad-tools or mixelpixx/KiCAD-MCP-Server), with attribution;
   see [Permitted third-party code](#permitted-third-party-code).

Nobody does both for the same capability.

## Hard rules for implementers

- **Never open, clone, search or paste from** `mixelpixx/Konnect`, its forks, or
  any AGPL or closed-source successor, including Konnect-EDA. That covers code
  search, tool-call output that quotes it, and asking an LLM to recall it.
- **Never read KiCad's GPL source** to copy behaviour. Use its public API docs and
  black-box runs of `kicad-cli` or KiCad. The same holds for any GPL-licensed
  code: it is black-box only, never read or reused.
- Do not copy test fixtures, schemas, prompt text or tool descriptions verbatim
  from the original project. Write your own.
- If you were exposed to the original source for a capability, say so on the
  issue and hand that capability to someone else.

## Permitted third-party code

Implementers may read and reuse code licensed under **MIT, BSD, ISC or
Apache-2.0**. The rules above for Konnect, its forks, Konnect-EDA and any AGPL
or closed-source successor still apply in full, and GPL code, including
KiCad's source, stays black-box only. Reuse is allowed provided:

- **Attribution is kept.** Every file, or portion of a file, taken from an
  upstream project keeps that file's copyright notice, and the source is
  listed in [NOTICE](NOTICE) with its license, the files taken and the
  upstream commit.
- **License text travels with the code.** The full license text of each
  reused source, verbatim and including its copyright line, is committed as
  `LICENSES/<source-id>.txt`, where `<source-id>` is the lowercase
  `owner-repo` slug of the upstream repository (for example
  `LICENSES/mixelpixx-kicad-mcp-server.txt`). It is added in the same PR
  that first reuses code from that source, and is not removed while any of
  that source's code remains in the repository. The `LICENSES/` directory is
  created by the first such PR.
- **NOTICE names the license file.** The [NOTICE](NOTICE) entry for a source
  names its `LICENSES/<source-id>.txt` file on a `License text:` line.
- **Apache-2.0 extras.** For Apache-2.0 code, also:
  - keep the Apache-2.0 license text in `LICENSES/<source-id>.txt`, as above;
  - reproduce the attribution notices of the upstream `NOTICE` file verbatim
    in our [NOTICE](NOTICE) entry, in its `Upstream NOTICE:` block, when
    upstream has a `NOTICE` file (write `none` there when it does not);
  - retain all copyright, patent, trademark and attribution notices in the
    files taken;
  - mark each changed file with a prominent notice that it was changed
    (Apache-2.0 section 4), by adding a short "Modified by openkonnect"
    comment in that file's header.
- **No mixing with Konnect.** No file may combine permissively licensed reused
  code with anything recalled from Konnect. A contributor who was exposed to
  Konnect source for a capability must not touch reused code for that same
  capability.
- **IPC-only.** openkonnect drives KiCad only through the IPC API. Nothing that
  depends on the `pcbnew` SWIG binding or on AT-SPI GUI automation is reused,
  whatever its license.

For example, **mixelpixx/KiCAD-MCP-Server**
(<https://github.com/mixelpixx/KiCAD-MCP-Server>) is a separate, earlier project
by the same authors, licensed MIT (Copyright (c) 2024 mixelpixx). It is not
Konnect. Its IPC code may be reused under these rules; its SWIG backend may not.

## Provenance

Each capability spec records its sources (URLs and dates of public docs, or the
black-box runs). Each implementation PR names the spec it implements and states
that the author followed these rules.
