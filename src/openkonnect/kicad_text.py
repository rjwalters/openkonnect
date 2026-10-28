"""Strict comparison of KiCad board text with a saved file (issue #37).

On KiCad 10.0.7 the editor text that ``SaveDocumentToString`` returns is never
byte-identical to the file KiCad's own save writes (measured in issue #23). The
two forms differ in exactly two ways, observed black box:

1. the editor text repeats ``(version …)``, ``(generator …)`` and
   ``(generator_version …)`` inside every ``(footprint …)``; the file does not;
2. the editor text writes short lists on one line; the file breaks them over
   several lines.

:func:`same_document` compares the two forms with only those differences
removed, and nothing else:

* Both sides are split into tokens: ``(``, ``)``, quoted strings and bare
  atoms. A quoted string is kept verbatim, quotes and escapes included, so
  whitespace inside it counts. A bare atom is kept verbatim, so ``1`` and
  ``1.0`` differ, and so do ``"x"`` and ``x``. Whitespace between tokens is
  dropped.
* A list of the exact form ``(version T)``, ``(generator T)`` or
  ``(generator_version T)``, with one non-list token ``T``, is dropped when it
  is a direct child of a list whose head is the bare atom ``footprint``. The
  same list anywhere else (the board header, a nested list inside a
  footprint) is kept.
* Any other difference, or text that does not tokenize (an unterminated
  string, unbalanced parentheses, two tokens run together), means "not the
  same document". This function never raises into a match.

The rule is applied only on the KiCad versions in
:data:`CANONICAL_MATCH_VERSIONS`, where the difference was measured. On every
other version the caller keeps byte equality (operator decision on issue #37).
"""

from __future__ import annotations

import re

#: KiCad versions (``KiCad.get_version().full_version``, exact string) on which
#: the editor-text-vs-file difference was measured and :func:`same_document` is
#: used. Add a version only after measuring it (see "Is the editor text
#: byte-identical to a KiCad save?" in ``docs/handoff-protocol.md``).
CANONICAL_MATCH_VERSIONS: frozenset[str] = frozenset({"10.0.7"})

#: Heads of the per-footprint header lists the editor text adds.
FOOTPRINT_HEADER_HEADS: frozenset[str] = frozenset({"version", "generator", "generator_version"})

#: ``match_basis`` values: how the editor text was shown equal to the file.
MATCH_BYTES = "bytes"
MATCH_CANONICAL = "canonical"

_WS = " \t\r\n"
# One token or one run of whitespace. Quoted strings may span lines and hold
# backslash escapes. A bare atom is any run without whitespace, parens or quotes.
_TOKEN = re.compile(r'[ \t\r\n]+|\(|\)|"(?:[^"\\]|\\.)*"|[^ \t\r\n()"]+', re.DOTALL)


class TokenizeError(ValueError):
    """The text is not a well-formed s-expression in KiCad's token syntax."""


def canonical_comparison_applies(kicad_version: str | None) -> bool:
    """Whether :func:`same_document` may be used against this KiCad version."""
    return kicad_version is not None and kicad_version in CANONICAL_MATCH_VERSIONS


def tokens(text: str) -> list[str]:
    """Split ``text`` into ``(``, ``)``, quoted strings and bare atoms, verbatim.

    Raises :class:`TokenizeError` on an unterminated string, unbalanced
    parentheses, or two atoms/strings with no whitespace between them (KiCad
    never writes that, so such text is not treated as comparable).
    """
    out: list[str] = []
    pos, n, depth = 0, len(text), 0
    prev_word = False  # the previous token was an atom or string, with no gap since
    for m in _TOKEN.finditer(text):
        if m.start() != pos:
            raise TokenizeError(f"unterminated string at offset {pos}")
        tok = m.group()
        pos = m.end()
        c = tok[0]
        if c in _WS:
            prev_word = False
            continue
        if c == "(":
            depth += 1
            prev_word = False
        elif c == ")":
            depth -= 1
            if depth < 0:
                raise TokenizeError(f"unbalanced ')' at offset {m.start()}")
            prev_word = False
        else:
            if prev_word:
                raise TokenizeError(f"tokens run together at offset {m.start()}")
            prev_word = True
        out.append(tok)
    if pos != n:
        raise TokenizeError(f"unterminated string at offset {pos}")
    if depth != 0:
        raise TokenizeError("unbalanced '(' at end of text")
    return out


def canonical_tokens(text: str) -> list[str]:
    """:func:`tokens` with the per-footprint header lists removed.

    Drops ``( head T )`` (``head`` in :data:`FOOTPRINT_HEADER_HEADS`, ``T`` one
    atom or string) only when it is a direct child of a list whose first token
    is the bare atom ``footprint``. Raises :class:`TokenizeError` like
    :func:`tokens`.
    """
    toks = tokens(text)
    out: list[str] = []
    # One entry per open list: True if that list is a (footprint …) list.
    stack: list[bool] = []
    i, n = 0, len(toks)
    while i < n:
        tok = toks[i]
        if tok == "(":
            if (
                stack
                and stack[-1]
                and i + 3 < n
                and toks[i + 1] in FOOTPRINT_HEADER_HEADS
                and toks[i + 2] not in ("(", ")")
                and toks[i + 3] == ")"
            ):
                i += 4  # a header list directly inside a footprint: drop it
                continue
            stack.append(i + 1 < n and toks[i + 1] == "footprint")
        elif tok == ")":
            stack.pop()
        out.append(tok)
        i += 1
    return out


def same_document(editor_text: str, file_bytes: bytes) -> bool:
    """Whether the editor text and the file hold the same board, by the strict rule.

    ``True`` only if both decode and tokenize and their canonical token
    sequences are equal. Undecodable bytes or malformed text give ``False``.
    """
    try:
        file_text = file_bytes.decode("utf-8")
        return canonical_tokens(editor_text) == canonical_tokens(file_text)
    except (UnicodeDecodeError, TokenizeError):
        return False
