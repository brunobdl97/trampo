"""tailor.py tests: the invented-facts guard (Review Focus 4) and the request's
cache prefix. The pipeline hook and the CLI are tested in test_pipeline.py and
test_cli.py, against their MockTransport fakes."""

from datetime import UTC, datetime
from pathlib import Path

from trampo.claude import load_prompt
from trampo.config import load_config
from trampo.models import JobRow, JobWithPostings, Posting
from trampo.resume import Resume, Work, load_resume
from trampo.resume.tailor import invented_facts, tailor_params

BASE = load_resume(Path("private.example/resume.json"))
CONFIG = load_config()


def _copy(resume: Resume = BASE) -> Resume:
    return resume.model_copy(deep=True)


def test_guard_flags_new_company() -> None:
    tailored = _copy()
    tailored.work[0].name = "Globex"

    [flagged] = invented_facts(BASE, tailored)

    assert "Globex" in flagged


def test_guard_flags_changed_dates() -> None:
    # Every number below already occurs in the Base resume: only the date rule can catch it.
    tailored = _copy()
    tailored.work[0].endDate = "2022-02"  # Nortech Pagamentos: current job in the base
    tailored.work[1].startDate = "2019-05"  # Rotamais Logística: 2019-06 in the base

    flagged = invented_facts(BASE, tailored)

    assert len(flagged) == 2
    assert "Nortech Pagamentos" in flagged[0]
    assert "Rotamais Logística" in flagged[1]


def test_guard_flags_new_number() -> None:
    tailored = _copy()
    tailored.work[0].highlights[0] = "Redesigned the settlement pipeline, cutting latency by 40%"

    [flagged] = invented_facts(BASE, tailored)

    assert "40" in flagged


def test_guard_normalizes_number_separators() -> None:
    base = _copy()
    base.work[0].highlights.append("Onboarded 1,000 merchants in 3.5 months")
    tailored = _copy(base)
    tailored.work[0].highlights[-1] = "Integrou 1.000 lojistas em 3,5 meses"
    tailored.work[1].highlights[0] = (
        "Construiu uma API com 50 mil requisições/dia e 99,95% de uptime"
    )

    assert invented_facts(base, tailored) == []


def test_guard_keywords_case_insensitive_against_whole_base_text() -> None:
    tailored = _copy()
    # "RabbitMQ" is only in a project description, never a skill keyword in the base.
    tailored.skills[0].keywords = ["fastapi", "POSTGRESQL", "RabbitMQ"]
    assert invented_facts(BASE, tailored) == []

    # Whole words only: "retail" in the base does not make "AI" a fact.
    tailored.skills[0].keywords += ["Kafka", "AI"]
    flagged = invented_facts(BASE, tailored)

    assert len(flagged) == 2
    assert "Kafka" in flagged[0]
    assert "AI" in flagged[1]


def test_guard_flags_changed_position() -> None:
    tailored = _copy()
    tailored.work[0].position = "Staff Engineer"
    tailored.work[1].position = "  backend ENGINEER "  # case and spacing are not facts

    [flagged] = invented_facts(BASE, tailored)

    assert "Staff Engineer" in flagged


def test_guard_flags_title_moved_across_a_promotion() -> None:
    # A promotion: two entries at one company. Each tailored entry must match one
    # base entry as a whole, not take the dates of one and the title of the other.
    base = _copy()
    base.work.insert(
        1,
        Work(
            name="Nortech Pagamentos",
            position="Backend Engineer",
            startDate="2020-01",
            endDate="2022-02",
        ),
    )
    assert invented_facts(base, _copy(base)) == []
    tailored = _copy(base)
    tailored.work[1].position = "Senior Backend Engineer"

    [flagged] = invented_facts(base, tailored)

    assert "Senior Backend Engineer" in flagged
    assert "2020-01" in flagged


def test_guard_flags_changed_education_dates() -> None:
    tailored = _copy()
    tailored.education[0].endDate = "2017-01"  # its numbers are in the base: only this rule

    [flagged] = invented_facts(BASE, tailored)

    assert "Universidade de São Paulo" in flagged


def test_guard_flags_changed_location() -> None:
    tailored = _copy()
    assert tailored.basics.location is not None
    tailored.basics.location.city = "Lisboa"
    tailored.basics.location.countryCode = "PT"

    flagged = invented_facts(BASE, tailored)

    assert len(flagged) == 2
    assert "Lisboa" in flagged[0]
    assert "PT" in flagged[1]

    omitted = _copy()
    omitted.basics.location = None
    assert invented_facts(BASE, omitted) == []


def test_guard_flags_changed_project_url() -> None:
    tailored = _copy()
    tailored.projects[0].url = "https://github.com/someone-else/queuewatch"

    [flagged] = invented_facts(BASE, tailored)

    assert "https://github.com/someone-else/queuewatch" in flagged

    omitted = _copy()
    omitted.projects[0].url = None
    assert invented_facts(BASE, omitted) == []


def test_guard_flags_changed_contact_data() -> None:
    tailored = _copy()
    tailored.basics.name = "Alex S. Silva"
    tailored.basics.email = "alex@another.example"
    tailored.basics.phone = "+55 11 5678-91234"  # every number is in the base: only this rule
    tailored.basics.label = "Staff Engineer"
    tailored.basics.profiles.append(tailored.basics.profiles[0].model_copy())
    tailored.basics.profiles[-1].url = "https://github.com/someone-else"

    flagged = invented_facts(BASE, tailored)

    assert len(flagged) == 5
    for value in ("Alex S. Silva", "alex@another.example", "5678-91234", "Staff Engineer"):
        assert any(value in fact for fact in flagged), value
    assert any("https://github.com/someone-else" in fact for fact in flagged)

    omitted = _copy()  # leaving optional contact data out states nothing new
    omitted.basics.phone = None
    omitted.basics.profiles = omitted.basics.profiles[:1]
    assert invented_facts(BASE, omitted) == []


def test_guard_flags_new_institution_and_project() -> None:
    tailored = _copy()
    tailored.education[0].institution = "MIT"
    tailored.projects[0].name = "kafkawatch"

    flagged = invented_facts(BASE, tailored)

    assert len(flagged) == 2
    assert "MIT" in flagged[0]
    assert "kafkawatch" in flagged[1]

    renamed_case = _copy()
    renamed_case.education[0].institution = "UNIVERSIDADE DE SÃO PAULO"
    renamed_case.projects[0].name = "QueueWatch"
    assert invented_facts(BASE, renamed_case) == []


def test_guard_allows_reorder_and_translation() -> None:
    tailored = _copy()
    tailored.basics.summary = (
        "Engenheiro backend com 6 anos construindo APIs de alto throughput em Go e Python."
    )
    tailored.work.reverse()  # reordered
    nortech = tailored.work[-1]
    nortech.highlights = [  # translated, one highlight omitted
        "Redesenhou o pipeline de liquidação, reduzindo a latência p99 em 42% "
        "e o custo de infraestrutura em 18%",
        "Migrou 12 microsserviços de Flask para FastAPI, aumentando o throughput em 30%",
    ]
    tailored.skills.reverse()
    tailored.skills[0].name = "Práticas"  # translated heading
    tailored.skills[0].keywords.reverse()
    tailored.projects = []  # omitted
    tailored.languages[0].language = "Português"

    assert invented_facts(BASE, tailored) == []


def _item(company: str, title: str) -> JobWithPostings:
    url = "https://example.com/jobs/1"
    posting = Posting(
        ats="greenhouse",
        board_slug="example",
        posting_id="1",
        company=company,
        title=title,
        location="Remote",
        url=url,
        description="Build APIs.",
        workplace="remote",
        salary=None,
        published_at=None,
    )
    job = JobRow(
        id=1,
        company=company,
        title=title,
        normalized_title=title.lower(),
        created_run_id=1,
        created_at=datetime(2026, 9, 30, tzinfo=UTC),
        closed_at=None,
        verdict="eligible",
        verdict_reason=None,
        track=None,
        fit_score=None,
        fit_reason=None,
        job_language=None,
        status="new",
        notes="",
        resume_path=None,
        locations=["Remote"],
        urls=[url],
    )
    return JobWithPostings(job=job, postings=[posting])


def test_params_prefix_identical_across_jobs_tracks_and_languages() -> None:
    prompt = load_prompt("tailor")
    backend, agents = CONFIG.tracks
    params_a = tailor_params(
        _item("Acme", "Backend Engineer"), BASE, backend, prompt, "claude-opus-5", "en"
    )
    params_b = tailor_params(
        _item("Globex", "AI Agent Engineer"), BASE, agents, prompt, "claude-opus-5", "pt"
    )

    assert params_a["system"] == params_b["system"]
    assert params_a["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params_a["thinking"] == {"type": "adaptive"}
    schema = params_a["output_config"]["format"]["schema"]
    assert set(schema["properties"]) == set(Resume.model_fields)
    [message_a] = params_a["messages"]
    [message_b] = params_b["messages"]
    assert "Acme" in message_a["content"] and backend.emphasis in message_a["content"]
    assert "English" in message_a["content"]
    assert agents.emphasis in message_b["content"]
    assert "Brazilian Portuguese" in message_b["content"]
