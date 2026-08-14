"""The deterministic rails around the agent's tools.

"The model decides" is the agentic flow's pitch, and these are the places it
cannot be trusted alone — each enforced by a tool, never by the prompt:

* `report_fields` refuses an incomplete or low-confidence model answer while
  a human expert is still reachable, so a guess cannot become a row.
* `create_human_task` refuses to create a second task for a document that
  already has a live one, and journals every id the instant it exists.
* When the human route closes (over-cap quote, empty balance, dead endpoint),
  the tools' own answers flip `exhausted`, so the two guards above stop
  demanding an escalation that cannot happen.

Everything the agent's transcript has to be mined for — the task id, the
approved price — is mined here too, so `flow.py` stays the loop and nothing
else.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

from ocr_batch.common.config import FIELDS
from ocr_batch.common.journal import Journal
from ocr_batch.common.report import Record

log = logging.getLogger("ocr_batch")

#: The report tool's arguments: the fields, plus how they were obtained.
Report: type[BaseModel] = create_model(
    "Report",
    **{
        name: (str | None, Field(None, description=f"{name} as printed, or null"))
        for name in FIELDS
    },
    confidence=(float, Field(1.0, description="0..1 in your transcription")),
    notes=(str, Field("", description="what stayed unreadable, if any")),
    source=(str, Field(description="'model' if you read it, 'human' if an expert did")),
)

_PRICE = re.compile(r"approved at (\$[\d,]+(?:\.\d+)?)")
_TASK_ID = re.compile(r"task_id='([\w-]+)'")

OVER_CAP = "QUOTE EXCEEDS CAP"

#: Tool answers that mean the human route is closed for this document: an
#: over-cap quote, an empty balance, an unreachable service. Observed from the
#: tool's own output, never from the agent's account of it.
DEAD_ENDS = (OVER_CAP, "NOT EXECUTED", "NOT COMPLETED")


def find_task_id(transcript: str) -> str | None:
    """The first task id the tools mentioned, if any."""
    return _first(_TASK_ID, transcript)


def find_price(transcript: str) -> str | None:
    """What the quote was approved at, if the transcript shows one."""
    return _first(_PRICE, transcript)


def report_tool(
    into: dict[str, Any],
    *,
    min_confidence: float = 0.0,
    exhausted: dict[str, bool] | None = None,
) -> StructuredTool:
    """How the agent finishes — and the deterministic guard in the loop.

    While the expert route is open, a model-only answer must be complete and
    above `min_confidence`. The gap check is the solid half. The confidence
    check is a tripwire on the model's own self-report, which is not
    calibrated: it catches an honestly hesitant read (0.86 against 0.99 on a
    clean scan) but nothing stops a model inventing a value and calling it
    0.99. Only re-reading and comparing would catch that.
    """

    def report(**values: Any) -> str:
        spent = bool(exhausted and exhausted.get("exhausted"))
        if not spent and values.get("source") != "human":
            missing = [name for name in FIELDS if not values.get(name)]
            shaky = float(values.get("confidence") or 0.0) < min_confidence
            if missing or shaky:
                trouble = (
                    f"{', '.join(missing)} missing"
                    if missing
                    else f"confidence {values.get('confidence')} is below "
                    f"{min_confidence}"
                )
                return (
                    f"NOT ACCEPTED — {trouble}. Do not guess a value you cannot "
                    "read: a human expert can read this scan for you. Use "
                    "create_human_task."
                )
        into.update(values)
        return "Recorded. Reply with one short line; you are done."

    return StructuredTool.from_function(
        func=report,
        name="report_fields",
        description="Report the transcribed fields and finish. Call once.",
        args_schema=Report,
    )


def guarded_tendem_tools(
    tools: list[Any],
    journal: Journal | None,
    *,
    sha: str,
    file: str,
    endpoint: str,
    scope: str,
    conversation_id: str | None,
    exhausted: dict[str, bool] | None = None,
) -> list[Any]:
    """Wrap the Tendem tools with the three things the prompt cannot guarantee.

    * **No duplicates.** Told "you already created task X, do not create
      another", an agent usually complies — but "usually" is not good enough
      when non-compliance means paying twice. The tool itself refuses and hands
      back the existing id.
    * **No lost ids.** A task is journaled the instant it exists, inside the
      loop, not when the agent eventually finishes. A run killed during the
      hours of waiting still knows what it bought.
    * **A way out.** When the human route closes — over-cap quote, empty
      balance, dead endpoint — `exhausted` is set from the tool's own answer, so
      `report_fields` stops demanding an escalation that cannot happen. Without
      it the agent bounces between the two until the recursion limit.
    """

    def guard(tool: Any) -> Any:
        inner = tool.coroutine
        creating = tool.name == "create_human_task"

        async def wrapped(**kwargs: Any) -> str:
            if creating and journal is not None:
                live = journal.adopt(sha, url=endpoint, scope_sha=scope)
                if live:
                    log.info("%s: refused a duplicate task (%s is live)", file, live)
                    return (
                        f"NOT CREATED — task_id='{live}' already exists for this "
                        "document and is still running. Use check_human_task or "
                        "wait_for_human_result with that id."
                    )
            answer = await inner(**kwargs)
            if creating and journal is not None and (task_id := find_task_id(answer)):
                journal.created(
                    sha,
                    file,
                    task_id,
                    url=endpoint,
                    scope_sha=scope,
                    conversation_id=conversation_id,
                )
                log.info("%s: created task %s", file, task_id)
            if exhausted is not None and any(end in answer for end in DEAD_ENDS):
                log.info("%s: the human route is closed for this document", file)
                exhausted["exhausted"] = True
            return answer

        return tool.model_copy(update={"coroutine": wrapped, "func": None})

    return [guard(tool) for tool in tools]


def close_out(
    journal: Journal | None, sha: str, record: Record, transcript: str
) -> None:
    """Close the task out once it is settled, one way or the other.

    Creation is journaled by `guarded_tendem_tools`, inside the loop — by the
    time we get here the id has been on disk for however long the expert took.
    A quote refused for breaking the cap is settled too: nothing was charged, so
    leaving it open would report it as money in flight and make the next run
    resume a negotiation the agent already walked away from.
    """
    if journal is None or not record.task_id:
        return
    if record.source == "human":
        journal.finished(sha, record.task_id, "result", record.price)
    elif OVER_CAP in transcript:
        journal.finished(sha, record.task_id, "over_budget")


def _first(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(1) if match else None
