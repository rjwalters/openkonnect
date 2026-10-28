"""The strict editor-text-vs-file comparison of issue #37 (``openkonnect.kicad_text``).

The equal cases are the two differences measured on KiCad 10.0.7 (issue #23):
the per-footprint ``version``/``generator``/``generator_version`` lines and the
line breaking of short lists. Everything else must stay a difference.
"""

from __future__ import annotations

import pytest

from openkonnect.kicad_text import (
    CANONICAL_MATCH_VERSIONS,
    TokenizeError,
    canonical_comparison_applies,
    canonical_tokens,
    same_document,
    tokens,
)

# Shaped like KiCad 10.0.7's two forms of one board (redacted from the #23 record).
FILE = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(footprint "openkonnect:R_Fixture"
\t\t(layer "F.Cu")
\t\t(uuid "0b6e9a1c-0000-4000-8000-000000000001")
\t\t(at 110 60)
\t\t(property "Reference" "R1"
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1 1)
\t\t\t\t\t(thickness 0.15)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(fp_line
\t\t\t(start -1 0)
\t\t\t(end 1 0)
\t\t\t(stroke
\t\t\t\t(width 0.1)
\t\t\t\t(type default)
\t\t\t)
\t\t)
\t)
)
"""

EDITOR = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(footprint "openkonnect:R_Fixture"
\t\t(version 20260206)
\t\t(generator "pcbnew")
\t\t(generator_version "10.0")
\t\t(layer "F.Cu")
\t\t(uuid "0b6e9a1c-0000-4000-8000-000000000001")
\t\t(at 110 60)
\t\t(property "Reference" "R1"
\t\t\t(effects (font (size 1 1) (thickness 0.15)))
\t\t)
\t\t(fp_line
\t\t\t(start -1 0)
\t\t\t(end 1 0)
\t\t\t(stroke (width 0.1) (type default))
\t\t)
\t)
)
"""


def same(editor: str, file: str) -> bool:
    return same_document(editor, file.encode("utf-8"))


# -- the measured differences are equal ---------------------------------------------


def test_kicad_10_0_7_save_and_editor_text_are_the_same_document():
    assert EDITOR.encode() != FILE.encode()
    assert same(EDITOR, FILE)


def test_header_lines_alone_or_line_breaking_alone_are_each_equal():
    one_line_file = FILE.replace(
        "(font\n\t\t\t\t\t(size 1 1)\n\t\t\t\t\t(thickness 0.15)\n\t\t\t\t)",
        "(font (size 1 1) (thickness 0.15))",
    )
    assert one_line_file != FILE
    assert same(EDITOR, one_line_file)
    assert same(FILE, one_line_file)


def test_identical_text_is_the_same_document():
    assert same(FILE, FILE)
    assert same(EDITOR, EDITOR)


def test_line_endings_and_trailing_newline_are_whitespace():
    assert same(EDITOR, FILE.replace("\n", "\r\n"))
    assert same(EDITOR, FILE.rstrip("\n"))


# -- anything else is a difference --------------------------------------------------


@pytest.mark.parametrize(
    "old, new",
    [
        pytest.param('"R1"', '"R 1"', id="whitespace added inside a quoted string"),
        pytest.param("(at 110 60)", "(at 110.0 60)", id="1 vs 1.0"),
        pytest.param('(layer "F.Cu")', "(layer F.Cu)", id='"x" vs x'),
        pytest.param("(at 110 60)", "(at 111 60)", id="changed coordinate"),
        pytest.param("(at 110 60)", "(at 110 60 90)", id="added atom"),
        pytest.param("\t\t(at 110 60)\n", "", id="removed node"),
        pytest.param('\t\t(uuid "0b6e9a1c-0000-4000-8000-000000000001")\n', "", id="missing uuid"),
        pytest.param(
            '(layer "F.Cu")\n\t\t(uuid "0b6e9a1c-0000-4000-8000-000000000001")',
            '(uuid "0b6e9a1c-0000-4000-8000-000000000001")\n\t\t(layer "F.Cu")',
            id="reordered nodes",
        ),
        pytest.param("(start -1 0)", "(start -1 0) (extra)", id="added node"),
        pytest.param("(generator_version", "(Generator_version", id="case of an atom"),
    ],
)
def test_any_other_change_is_not_the_same_document(old, new):
    assert old in FILE
    changed = FILE.replace(old, new, 1)
    assert not same(EDITOR, changed)
    assert not same(changed, FILE)


def test_a_b_vs_ab_in_a_quoted_string_differ():
    assert not same('(a "A B")', '(a "AB")')
    assert not same('(a "A B")', '(a "A  B")')
    assert not same('(a "A\tB")', '(a "A B")')


def test_split_or_joined_atoms_differ():
    assert not same("(a b c)", "(a bc)")


def test_header_line_outside_a_footprint_is_not_ignored():
    # The board header (direct child of kicad_pcb) is kept.
    assert not same(FILE, FILE.replace('\t(generator "pcbnew")\n', "", 1))
    # The same list under a non-footprint list is kept.
    assert not same('(kicad_pcb (net 1 "VIN") (version 1))', '(kicad_pcb (net 1 "VIN"))')
    assert not same('(kicad_pcb (property "x" (version 1)))', '(kicad_pcb (property "x"))')


def test_header_line_nested_deeper_inside_a_footprint_is_not_ignored():
    with_nested = '(kicad_pcb (footprint "f" (property "p" (generator "pcbnew"))))'
    assert not same(with_nested, '(kicad_pcb (footprint "f" (property "p")))')


def test_only_the_exact_header_shape_is_ignored():
    base = '(kicad_pcb (footprint "f" (layer "F.Cu")))'
    # One atom or string after the head: dropped.
    assert same('(kicad_pcb (footprint "f" (version 2) (layer "F.Cu")))', base)
    # Two values, no value, or a nested list: kept.
    assert not same('(kicad_pcb (footprint "f" (version 2 3) (layer "F.Cu")))', base)
    assert not same('(kicad_pcb (footprint "f" (version) (layer "F.Cu")))', base)
    assert not same('(kicad_pcb (footprint "f" (version (x)) (layer "F.Cu")))', base)
    # Other heads: kept.
    assert not same('(kicad_pcb (footprint "f" (tstamp 2) (layer "F.Cu")))', base)


def test_quoted_footprint_head_is_not_a_footprint():
    assert not same('(kicad_pcb ("footprint" "f" (version 2)))', '(kicad_pcb ("footprint" "f"))')


def test_header_lines_are_ignored_on_either_side():
    a = '(kicad_pcb (footprint "f" (version 2) (layer "F.Cu")))'
    b = '(kicad_pcb (footprint "f" (generator "x") (layer "F.Cu")))'
    assert same(a, b)


# -- malformed input is never a match -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        pytest.param(FILE[: len(FILE) // 2], id="truncated text"),
        pytest.param(FILE + ")", id="extra close paren"),
        pytest.param('(a "unterminated)', id="unterminated string"),
        pytest.param('(a b"c")', id="atom and string run together"),
        pytest.param('(a "b""c")', id="two strings run together"),
    ],
)
def test_malformed_text_raises_and_never_matches(text):
    with pytest.raises(TokenizeError):
        tokens(text)
    assert not same(text, text)
    assert not same(EDITOR, text)


def test_undecodable_file_bytes_never_match():
    assert not same_document("(a)", b"(a \xff)")


# -- the tokenizer keeps tokens verbatim --------------------------------------------


def test_tokens_are_verbatim():
    text = '(a 1 1.0 "x y" x "q\\"uote" "line\nbreak" (b))'
    assert tokens(text) == [
        "(",
        "a",
        "1",
        "1.0",
        '"x y"',
        "x",
        '"q\\"uote"',
        '"line\nbreak"',
        "(",
        "b",
        ")",
        ")",
    ]


def test_canonical_tokens_drop_only_footprint_headers():
    out = canonical_tokens('(kicad_pcb (version 1) (footprint "f" (version 1) (at 0 0)))')
    assert out == [
        "(",
        "kicad_pcb",
        "(",
        "version",
        "1",
        ")",
        "(",
        "footprint",
        '"f"',
        "(",
        "at",
        "0",
        "0",
        ")",
        ")",
        ")",
    ]


# -- version allow-list ---------------------------------------------------------------


def test_allow_list_is_the_measured_version_only():
    assert sorted(CANONICAL_MATCH_VERSIONS) == ["10.0.7"]
    assert canonical_comparison_applies("10.0.7")


@pytest.mark.parametrize("version", [None, "", "10.0.6", "10.0.8", "10.0", "10.0.7-1", "11.0.0"])
def test_unlisted_versions_do_not_apply(version):
    assert not canonical_comparison_applies(version)
