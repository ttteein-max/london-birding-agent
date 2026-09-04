"""Conservative request hints shared by parsing, HITL views, and resume validation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.biodiversity.agent_models import ExpeditionRequestDraft


_NUMBER_WORDS = {
    "one": 1.0,
    "two": 2.0,
    "three": 3.0,
    "four": 4.0,
    "five": 5.0,
    "six": 6.0,
    "seven": 7.0,
    "eight": 8.0,
}


def request_text_hints(original: str) -> ExpeditionRequestDraft:
    """Copy only explicit, bounded fields when provider structure is unusable."""

    postcode_match = re.search(
        r"\b([A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2})\b",
        original,
        re.IGNORECASE,
    )
    duration_match = re.search(
        r"\b(\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight)"
        r"[ -]hours?\b",
        original,
        re.IGNORECASE,
    )
    duration = None
    if duration_match:
        token = duration_match.group(1).casefold()
        duration = _NUMBER_WORDS.get(token)
        if duration is None:
            duration = float(token)

    bird_match = re.search(
        r"\b(?:to\s+)?(?:spot|search\s+for|look(?:ing)?\s+for|observe|see)\s+"
        r"(.+?)(?:[.?!]|$)",
        original,
        re.IGNORECASE,
    )
    bird_input = None
    if bird_match:
        bird_input = bird_match.group(1).strip(" ,.")
        bird_input = re.sub(
            r"^(?:a|an|the)\s+", "", bird_input, flags=re.IGNORECASE
        )
        bird_input = re.sub(
            r"^(?:vagrant|rare)\s+", "", bird_input, flags=re.IGNORECASE
        )

    location_query = None
    if postcode_match is None:
        location_match = re.search(
            r"\b(?:starting\s+(?:near|from)|start(?:ing)?\s+(?:near|from)|near|from)\s+"
            r"(.+?)(?=\s+(?:on\b|next\b|this\b|tomorrow\b|today\b|"
            r"to\s+(?:spot|search|look|observe|see)\b)|[,.?!]|$)",
            original,
            re.IGNORECASE,
        )
        if location_match:
            location_query = location_match.group(1).strip(" ,.")

    target_date = None
    iso_date = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", original)
    if iso_date:
        try:
            target_date = datetime.strptime(iso_date.group(1), "%Y-%m-%d").date()
        except ValueError:
            target_date = None
    else:
        long_date = re.search(
            r"\b(\d{1,2})\s+"
            r"(January|February|March|April|May|June|July|August|September|October|November|December)"
            r"\s+(20\d{2})\b",
            original,
            re.IGNORECASE,
        )
        if long_date:
            try:
                target_date = datetime.strptime(
                    " ".join(long_date.groups()), "%d %B %Y"
                ).date()
            except ValueError:
                target_date = None

    return ExpeditionRequestDraft(
        bird_input=bird_input,
        postcode=postcode_match.group(1).upper() if postcode_match else None,
        location_query=location_query,
        target_local_date=target_date,
        duration_hours=duration,
    )


def merge_request_draft(
    primary: ExpeditionRequestDraft | dict[str, Any] | None,
    original: str,
) -> ExpeditionRequestDraft | None:
    """Fill only absent draft fields from explicit text without overriding state."""

    if isinstance(primary, ExpeditionRequestDraft):
        values = primary.model_dump(mode="python")
    else:
        values = dict(primary or {})
    fallback_values = request_text_hints(original).model_dump(
        mode="python",
        exclude_none=True,
    )
    for name, value in fallback_values.items():
        if values.get(name) is None or values.get(name) == "":
            values[name] = value
    if not any(value is not None and value != "" for value in values.values()):
        return None
    return ExpeditionRequestDraft.model_validate(values)
