"""Optional LLM pass over candidate clips.

``FEATURES.md`` allows a small optional LLM API for the MVP, with a transparent
heuristic fallback. This module is that optional pass, and it is **off unless a
key is configured**; the project must work with no key, no network, and no paid
service (``PROJECT.md``).

Two deliberate constraints keep the optional path safe:

* The model may **rewrite titles, reasons, and scores, but never timestamps**.
  Clip boundaries stay the transcript-derived windows, so a hallucinated time
  range can never reach the timeline or the renderer.
* Any failure — missing key, network error, non-200, unparseable body — returns
  ``None`` and the caller keeps the heuristic result silently, because an
  unavailable extra must not break the core workflow.

Only the standard library is used, so enabling this adds no dependency weight.
The API key is read server-side from the environment and is never sent to the
browser.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
REQUEST_TIMEOUT_SECONDS = 30

#: How much of each candidate is sent. Enough context to judge a hook without
#: shipping a whole transcript to a third party.
MAX_EXCERPT_CHARACTERS = 700
MAX_TITLE_CHARACTERS = 90

SYSTEM_PROMPT = (
    "You label candidate moments from a video transcript for short-form vertical "
    "clips. Reply with JSON only, in the exact shape asked for. Write titles from "
    "the speaker's own words. Score each clip from 0 to 1 for how self-contained "
    "and information-dense it is. Never claim a clip will go viral."
)

#: Transport is injectable so tests never touch the network.
Opener = Callable[[urllib.request.Request, int], tuple[int, bytes]]


@dataclass(frozen=True)
class LlmSettings:
    """Server-side LLM configuration."""

    api_key: str | None = None
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_seconds: int = REQUEST_TIMEOUT_SECONDS

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and str(self.api_key).strip())


def _urlopen(request: urllib.request.Request, timeout: int) -> tuple[int, bytes]:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return int(response.status), response.read()


def build_prompt(candidates: list[dict]) -> str:
    """Describe the candidates that the model is allowed to re-label."""

    blocks: list[str] = []
    for candidate in candidates:
        excerpt = " ".join(str(candidate.get("_text") or "").split())[:MAX_EXCERPT_CHARACTERS]
        blocks.append(
            f"id: {candidate['id']}\n"
            f"start: {float(candidate['source_start']):.1f}s "
            f"end: {float(candidate['source_end']):.1f}s\n"
            f"transcript: {excerpt}"
        )

    return (
        "Re-label these candidate clips. Do not change their start or end times.\n\n"
        + "\n\n".join(blocks)
        + '\n\nReply with JSON only: {"clips":[{"id":"clip-1","title":"...",'
        '"reason":"...","score":0.0}]}'
    )


def extract_json_object(content: str) -> object | None:
    """Pull a JSON object out of a response that may be fenced in markdown.

    Shared with :mod:`app.broll_ai`, which asks the same endpoint a different
    question and has to survive the same chatty answers.
    """

    text = content.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if text.count("```") >= 2 else text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Fall back to the outermost braces rather than giving up on a chatty model.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def parse_ranking(body: bytes, valid_ids: set[str]) -> dict[str, dict] | None:
    """Turn a chat-completions body into ``{clip_id: overrides}``.

    Anything unexpected — bad JSON, unknown ids, wrong types — is dropped rather
    than trusted, and an empty result means "not usable".
    """

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
        content = payload["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, AttributeError):
        return None

    data = extract_json_object(content if isinstance(content, str) else "")
    if not isinstance(data, dict):
        return None

    raw_clips = data.get("clips")
    if not isinstance(raw_clips, list):
        return None

    ranking: dict[str, dict] = {}
    for item in raw_clips:
        if not isinstance(item, dict):
            continue
        clip_id = str(item.get("id") or "")
        if clip_id not in valid_ids:
            # The model may only re-label clips we actually generated.
            continue

        entry: dict[str, object] = {}
        title = " ".join(str(item.get("title") or "").split())[:MAX_TITLE_CHARACTERS]
        if title:
            entry["title"] = title
        reason = " ".join(str(item.get("reason") or "").split())[:400]
        if reason:
            entry["reason"] = reason
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            score = None
        if score is not None:
            entry["score"] = round(min(1.0, max(0.0, score)), 3)

        if entry:
            ranking[clip_id] = entry

    return ranking or None


def request_ranking(
    settings: LlmSettings, candidates: list[dict], *, opener: Opener | None = None
) -> dict[str, dict] | None:
    """Ask the configured endpoint for labels, returning None on any problem."""

    if not settings.enabled or not candidates:
        return None

    body = json.dumps(
        {
            "model": settings.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_prompt(candidates)},
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(  # noqa: S310
        f"{settings.base_url.rstrip('/')}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {str(settings.api_key).strip()}",
        },
        method="POST",
    )

    try:
        status, response_body = (opener or _urlopen)(request, settings.timeout_seconds)
    except Exception:  # noqa: BLE001 - an optional extra must never break the flow
        return None

    if status != 200:
        return None

    return parse_ranking(response_body, {str(item["id"]) for item in candidates})


def make_ranker(settings: LlmSettings, *, opener: Opener | None = None):
    """Build the callback the heuristic calls to optionally re-label candidates.

    The callback returns the generator label to record, or ``None`` when the
    optional pass produced nothing usable.
    """

    def rank(candidates: list[dict]) -> str | None:
        ranking = request_ranking(settings, candidates, opener=opener)
        if not ranking:
            return None

        applied = False
        for candidate in candidates:
            overrides = ranking.get(str(candidate["id"]))
            if not overrides:
                continue
            if "title" in overrides:
                candidate["title"] = overrides["title"]
                applied = True
            if "reason" in overrides:
                # Keep the labelled reason instead of regenerating it.
                candidate["_reason_override"] = overrides["reason"]
                applied = True
            if "score" in overrides:
                candidate["score"] = overrides["score"]
                applied = True

        return "llm" if applied else None

    return rank
