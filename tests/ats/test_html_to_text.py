"""html_to_text unit tests: void-tag parity and HTML whitespace collapsing.

Lever and Ashby (Tasks 6-7) reuse this function, so it is tested directly
against synthetic HTML, not only through the Greenhouse fixture.
"""

from trampo.ats import html_to_text


def test_self_closed_void_tag_matches_open_form() -> None:
    # <br> (no closing tag) vs <br/> (self-closed) must produce the same break.
    assert html_to_text("<p>A<br>B</p>") == "A\nB"
    assert html_to_text("<p>A<br/>B</p>") == "A\nB"


def test_whitespace_collapses_across_inline_tags() -> None:
    html = "<p>This is <strong>bold\ntext</strong> spanning lines.</p>"
    assert html_to_text(html) == "This is bold text spanning lines."


def test_whitespace_runs_collapse_to_one_space() -> None:
    assert html_to_text("Some text  with   extra   spaces.") == "Some text with extra spaces."
