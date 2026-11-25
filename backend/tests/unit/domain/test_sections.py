from cerebro.domain.sections import extract_headings, extract_section

BODY = """# Title

Intro.

## Setup steps

Do this.

### Detail

Deeper.

## Other

Elsewhere.
"""


def test_headings_have_level_title_and_line() -> None:
    headings = extract_headings(BODY)
    assert [(h.level, h.title, h.line) for h in headings] == [
        (1, "Title", 0),
        (2, "Setup steps", 4),
        (3, "Detail", 8),
        (2, "Other", 12),
    ]


def test_section_includes_subsections_until_same_or_higher_level() -> None:
    assert extract_section(BODY, "Setup steps") == "## Setup steps\n\nDo this.\n\n### Detail\n\nDeeper.\n"


def test_last_section_runs_to_the_end() -> None:
    assert extract_section(BODY, "Other") == "## Other\n\nElsewhere.\n"


def test_top_level_section_includes_everything_below() -> None:
    assert extract_section(BODY, "Title") == BODY


def test_matching_ignores_case_and_extra_whitespace() -> None:
    assert extract_section(BODY, "  setup   STEPS ") is not None


def test_missing_heading_returns_none() -> None:
    assert extract_section(BODY, "Nope") is None
    assert extract_section("", "Nope") is None


def test_closing_hashes_and_indentation_are_handled() -> None:
    body = "  ## Closed ##\ntext\n"
    assert [h.title for h in extract_headings(body)] == ["Closed"]


def test_headings_in_fenced_code_are_ignored() -> None:
    body = "# Real\n```\n# not a heading\n```\n~~~~\n## nor this\n~~~~\nafter\n"
    assert [h.title for h in extract_headings(body)] == ["Real"]
    assert extract_section(body, "not a heading") is None


def test_hash_without_space_is_not_a_heading() -> None:
    assert extract_headings("#tag\n####### seven\n") == ()


def test_first_matching_heading_wins() -> None:
    body = "## A\none\n## A\ntwo\n"
    assert extract_section(body, "A") == "## A\none\n"


def test_fence_closes_only_with_matching_marker() -> None:
    body = "```\n~~~\n# inside\n```\n# outside\n"
    assert [h.title for h in extract_headings(body)] == ["outside"]
