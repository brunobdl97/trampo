"""Claude API foundation: client factory, newest-Opus resolution, prompt
loading/hashing, Message Batches helpers, a regular-call helper, and
spend-limit error mapping.

Every API call made through this module goes through `_call`, which maps
the "spend limit reached" API error onto `SpendLimitReached`; everything
else propagates unchanged.
"""

import hashlib
import importlib.resources
from collections.abc import Callable
from dataclasses import dataclass

import anthropic
import httpx2
from anthropic.types import Message
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from anthropic.types.messages.message_batch_canceled_result import MessageBatchCanceledResult
from anthropic.types.messages.message_batch_errored_result import MessageBatchErroredResult
from anthropic.types.messages.message_batch_expired_result import MessageBatchExpiredResult
from anthropic.types.messages.message_batch_succeeded_result import MessageBatchSucceededResult

PROMPTS = importlib.resources.files("trampo") / "prompts"


class SpendLimitReached(Exception):
    """The workspace/org spend limit, or the org's tier cap, was reached."""


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    hash: str  # sha256(text)[:12]


def _spend_limit_message(exc: anthropic.APIStatusError) -> str | None:
    """The spend-limit message if `exc` is a workspace/org spend limit (400)
    or a tier cap (429), else None."""
    body = exc.body
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if not isinstance(message, str):
        return None

    if isinstance(exc, anthropic.BadRequestError):
        if message.startswith("You have reached your specified"):
            return message
    elif isinstance(exc, anthropic.RateLimitError):
        details = error.get("details")
        if (
            isinstance(details, dict)
            and details.get("error_code") == "enforced_spend_limit_reached"
        ):
            return message
    return None


def _call[T](fn: Callable[[], T]) -> T:
    """Run one API call, translating a spend-limit error into SpendLimitReached."""
    try:
        return fn()
    except (anthropic.BadRequestError, anthropic.RateLimitError) as exc:
        message = _spend_limit_message(exc)
        if message is not None:
            raise SpendLimitReached(message) from exc
        raise


def make_client(http: httpx2.Client | None = None) -> anthropic.Anthropic:
    """Build an Anthropic client. Resolves ANTHROPIC_API_KEY from the
    environment; tests pass a MockTransport-backed httpx2.Client via `http`."""
    return anthropic.Anthropic(http_client=http)


def newest_opus(client: anthropic.Anthropic) -> str:
    """The id of the newest `claude-opus-*` model, via the Models API."""
    models = _call(lambda: list(client.models.list()))
    opus_models = [m for m in models if m.id.startswith("claude-opus-")]
    if not opus_models:
        raise RuntimeError("No claude-opus-* model available to this API key")
    return max(opus_models, key=lambda m: m.created_at).id


def load_prompt(name: str) -> Prompt:
    """Read src/trampo/prompts/<name>.md and hash its contents."""
    text = (PROMPTS / f"{name}.md").read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return Prompt(name=name, text=text, hash=digest)


def submit_batch(client: anthropic.Anthropic, requests: dict[str, dict]) -> str:
    """Submit a Message Batch; `requests` maps custom_id -> Messages params.
    Returns the batch id."""
    batch_requests = [
        Request(custom_id=cid, params=MessageCreateParamsNonStreaming(**params))
        for cid, params in requests.items()
    ]
    batch = _call(lambda: client.messages.batches.create(requests=batch_requests))
    return batch.id


def batch_results(client: anthropic.Anthropic, batch_id: str) -> dict[str, Message | str] | None:
    """Batch results keyed by custom_id. None while still processing.
    succeeded -> the Message; errored -> "<error type>: <message>";
    canceled/expired -> "canceled"/"expired"."""
    batch = _call(lambda: client.messages.batches.retrieve(batch_id))
    if batch.processing_status != "ended":
        return None

    results: dict[str, Message | str] = {}
    for item in _call(lambda: list(client.messages.batches.results(batch_id))):
        match item.result:
            case MessageBatchSucceededResult(message=message):
                results[item.custom_id] = message
            case MessageBatchErroredResult(error=error):
                results[item.custom_id] = f"{error.error.type}: {error.error.message}"
            case MessageBatchCanceledResult():
                results[item.custom_id] = "canceled"
            case MessageBatchExpiredResult():
                results[item.custom_id] = "expired"
    return results


def create_message(client: anthropic.Anthropic, params: dict) -> Message:
    """A regular (non-batch) Messages API call, through the spend-limit wrapper.
    Used by Discovery, on-demand Tailored resumes and eval."""
    return _call(lambda: client.messages.create(**params))
