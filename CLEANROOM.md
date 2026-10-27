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
   API documentation, and from MIT- or BSD-licensed code (for example
   kicad-tools, with attribution).

Nobody does both for the same capability.

## Hard rules for implementers

- **Never open, clone, search or paste from** `mixelpixx/Konnect`, its forks, or
  any AGPL or closed-source successor, including Konnect-EDA. That covers code
  search, tool-call output that quotes it, and asking an LLM to recall it.
- **Never read KiCad's GPL source** to copy behaviour. Use its public API docs and
  black-box runs of `kicad-cli` or KiCad.
- Do not copy test fixtures, schemas, prompt text or tool descriptions verbatim
  from the original project. Write your own.
- If you were exposed to the original source for a capability, say so on the
  issue and hand that capability to someone else.

## Provenance

Each capability spec records its sources (URLs and dates of public docs, or the
black-box runs). Each implementation PR names the spec it implements and states
that the author followed these rules.
