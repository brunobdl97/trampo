"""Tests for src/trampo/resume/render.py: HTML/PDF rendering of the Base resume."""

import re
from pathlib import Path

from trampo.resume import load_resume
from trampo.resume.render import html_to_pdf, render_html, resume_filename

_RESUME = load_resume(Path("private.example/resume.json"))


def test_html_has_every_work_entry() -> None:
    html = render_html(_RESUME, "en")
    for work in _RESUME.work:
        assert work.position in html
        assert work.name in html


def test_pt_headings() -> None:
    html = render_html(_RESUME, "pt")
    assert "Experiência" in html
    assert "Formação" in html
    assert "Habilidades" in html


def test_no_tables_or_images() -> None:
    html = render_html(_RESUME, "en").lower()
    assert "<table" not in html
    assert "<img" not in html


def test_filename_slug() -> None:
    assert resume_filename("Acme", "Senior Backend Engineer") == "acme-senior-backend-engineer.pdf"
    assert (
        resume_filename("São Paulo Tech", "Engenheiro Sênior")
        == "sao-paulo-tech-engenheiro-senior.pdf"
    )


def test_pdf_written(tmp_path: Path) -> None:
    html = render_html(_RESUME, "en")
    out = tmp_path / "base-en.pdf"
    html_to_pdf(html, out)
    assert out.read_bytes().startswith(b"%PDF")


def test_no_external_urls_other_than_profile_links() -> None:
    html = render_html(_RESUME, "en")
    profile_urls = {p.url for p in _RESUME.basics.profiles}
    found = set(re.findall(r'https?://\S+?(?=["\s<])', html))
    assert found  # sanity: the profile links are actually present
    assert found <= profile_urls


def test_headings_and_entries_avoid_page_breaks() -> None:
    """A section heading must stay with its content, and an entry/highlight
    must not split across pages (Task 15 fix round 1)."""
    html = render_html(_RESUME, "en")
    assert "break-after: avoid" in html
    assert "page-break-after: avoid" in html
    assert "break-inside: avoid" in html
    assert "page-break-inside: avoid" in html
