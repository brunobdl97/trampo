"""Render a Resume to ATS-friendly HTML and print the HTML to a PDF."""

import re
import unicodedata
from pathlib import Path
from typing import Literal

from jinja2 import Environment, PackageLoader, select_autoescape
from playwright.sync_api import sync_playwright

from trampo.resume.model import Basics, Resume

_ENV = Environment(
    loader=PackageLoader("trampo.resume", "templates"),
    autoescape=select_autoescape(["html"]),
)

_HEADINGS: dict[Literal["en", "pt"], dict[str, str]] = {
    "en": {
        "summary": "Summary",
        "experience": "Experience",
        "projects": "Projects",
        "education": "Education",
        "skills": "Skills",
        "languages": "Languages",
        "present": "Present",
    },
    "pt": {
        "summary": "Resumo",
        "experience": "Experiência",
        "projects": "Projetos",
        "education": "Formação",
        "skills": "Habilidades",
        "languages": "Idiomas",
        "present": "Atual",
    },
}


def _contact_items(basics: Basics) -> list[dict[str, str | None]]:
    """The header contact line: email, phone, location, then each profile
    URL as a visible link. Only present fields are included."""
    items: list[dict[str, str | None]] = [{"text": basics.email, "url": None}]
    if basics.phone:
        items.append({"text": basics.phone, "url": None})
    if basics.location:
        location = ", ".join(p for p in (basics.location.city, basics.location.countryCode) if p)
        if location:
            items.append({"text": location, "url": None})
    for profile in basics.profiles:
        items.append({"text": profile.url, "url": profile.url})
    return items


def render_html(resume: Resume, lang: Literal["en", "pt"]) -> str:
    template = _ENV.get_template("resume.html")
    headings = _HEADINGS[lang]
    return template.render(
        lang=lang,
        resume=resume,
        headings=headings,
        present=headings["present"],
        contact_items=_contact_items(resume.basics),
    )


def html_to_pdf(html: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p, p.chromium.launch() as browser:
        page = browser.new_page()
        page.set_content(html)
        page.pdf(path=str(out), format="A4", print_background=True)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return _SLUG_RE.sub("-", ascii_text.lower()).strip("-")


def resume_filename(company: str, title: str) -> str:
    return f"{_slug(company)}-{_slug(title)}.pdf"
