from datetime import UTC, datetime, timedelta

from trampo.config import load_config
from trampo.models import Posting
from trampo.prefilter import Dropped, prefilter

CONFIG = load_config()
NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def _posting(**overrides: object) -> Posting:
    defaults: dict[str, object] = {
        "ats": "greenhouse",
        "board_slug": "acme",
        "posting_id": "p1",
        "company": "Acme",
        "title": "Backend Engineer",
        "location": "Remote",
        "url": "https://acme.example/p1",
        "description": "Great job.",
        "workplace": "remote",
        "salary": None,
        "published_at": NOW,
    }
    defaults.update(overrides)
    return Posting.model_validate(defaults)


def test_internal_tools_is_not_intern() -> None:
    posting = _posting(title="Backend Engineer, Internal Tools")
    assert prefilter(posting, NOW, CONFIG) == "backend"


def test_django_is_not_go_engineer() -> None:
    posting = _posting(title="Django Engineer")
    assert prefilter(posting, NOW, CONFIG) == Dropped("no track keyword")


def test_software_engineer_needs_go() -> None:
    with_go = _posting(title="Software Engineer", description="We use Go and Kafka")
    assert prefilter(with_go, NOW, CONFIG) == "backend"

    go_to_market = _posting(title="Software Engineer", description="our go-to-market team")
    assert prefilter(go_to_market, NOW, CONFIG) == Dropped("no track keyword")

    golang = _posting(title="Software Engineer", description="Backend built in golang.")
    assert prefilter(golang, NOW, CONFIG) == "backend"


def test_agent_titles() -> None:
    posting = _posting(title="AI Agent Engineer")
    assert prefilter(posting, NOW, CONFIG) == "agents"


def test_automation_engineer_kept() -> None:
    posting = _posting(title="Automation Engineer")
    assert prefilter(posting, NOW, CONFIG) == "agents"


def test_ignored_keyword() -> None:
    posting = _posting(title="Senior Frontend Engineer")
    assert prefilter(posting, NOW, CONFIG) == Dropped("ignored title keyword: frontend")


def test_hybrid_dropped() -> None:
    posting = _posting(workplace="hybrid")
    assert prefilter(posting, NOW, CONFIG) == Dropped("not remote")


def test_old_posting_dropped() -> None:
    posting = _posting(published_at=NOW - timedelta(days=8))
    assert prefilter(posting, NOW, CONFIG) == Dropped("older than 7 days")


def test_undated_posting_kept() -> None:
    posting = _posting(published_at=None)
    assert prefilter(posting, NOW, CONFIG) == "backend"
