"""Step one: read a document with the LLM, and let it admit when it can't."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from ocr_batch.common.config import FIELDS
from ocr_batch.common.documents import as_data_url

PROMPT = f"""You are an OCR extraction step in an automated pipeline.

Read the attached invoice image and transcribe exactly what is printed on it.
Never guess, never infer a plausible value, never complete a value you can
only partly see — a wrong value is far more expensive than an admitted gap.

Return ONLY a JSON object, no prose, no code fences:

{{
  "fields": {{
{chr(10).join(f'    "{name}": <string or null>,' for name in FIELDS)}
  }},
  "confidence": <number 0..1 — your confidence that EVERY field above is
                 transcribed correctly>,
  "notes": "<what is illegible or missing, if anything>"
}}

issue_date must be YYYY-MM-DD, total_amount digits only (e.g. 1234.56),
currency an ISO 4217 code. Use null for anything you cannot read with
certainty, and lower the confidence accordingly."""


@dataclass(frozen=True)
class Extraction:
    """What the model got out of one document."""

    path: Path
    fields: dict[str, str | None]
    confidence: float
    notes: str

    def is_confident(self, threshold: float) -> bool:
        """Good enough to ship without a human looking at it?"""
        return self.confidence >= threshold and all(
            self.fields.get(name) for name in FIELDS
        )


async def extract(llm: BaseChatModel, path: Path) -> Extraction:
    """Run the vision model over one document."""
    message = HumanMessage(
        content=[
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": as_data_url(path)}},
        ]
    )
    try:
        response = await llm.ainvoke([message])
        payload = parse_json(response.text)
    except Exception as exc:  # noqa: BLE001 - a failed read escalates, not crashes
        return Extraction(path, dict.fromkeys(FIELDS), 0.0, f"model call failed: {exc}")

    fields = payload.get("fields")
    if not isinstance(fields, dict):
        fields = payload  # some models skip the wrapper
    return Extraction(
        path=path,
        fields={name: clean_field(fields.get(name)) for name in FIELDS},
        confidence=_as_float(payload.get("confidence")),
        notes=str(payload.get("notes") or ""),
    )


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_json(text: str) -> dict[str, Any]:
    """The first JSON object in a model reply — tolerant of fences and prose."""
    match = _JSON_BLOCK.search(text or "")
    if not match:
        return {}
    try:
        payload = json.loads(match.group())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def clean_field(value: Any) -> str | None:
    """A field value, or `None` for the many ways a model writes "missing"."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in {"null", "none", "n/a", "-"} else None


def _as_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0
