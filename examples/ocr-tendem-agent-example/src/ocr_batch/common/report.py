"""One row per document, written to CSV."""

from __future__ import annotations

import csv
import logging
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ocr_batch.common.config import FIELDS

log = logging.getLogger("ocr_batch")

COLUMNS = ("file", *FIELDS, "source", "confidence", "price", "task_id", "notes")

#: Rows we do not need to look at again on a restart.
SETTLED_SOURCES = ("model", "human")


@dataclass
class Record:
    """The outcome for one document, whoever produced it."""

    file: str
    #: "model" and "human" are answers; "processing" means a run is working on
    #: it right now (or was interrupted — the task is still live server-side);
    #: "failed" means this run gave up, with the reason first in `notes`.
    source: str  # "model" | "human" | "processing" | "failed"
    fields: dict[str, str | None] = field(default_factory=dict)
    confidence: float = 0.0
    price: str | None = None
    task_id: str | None = None
    notes: str = ""

    @classmethod
    def from_report(
        cls,
        file: str,
        reported: dict[str, object],
        *,
        task_id: str | None = None,
        price: str | None = None,
        min_confidence: float = 0.0,
    ) -> Record:
        """Build a row from what the agent's `report_fields` tool recorded.

        A row only claims to be a reading if it is complete and the model was
        confident. Anything else is `failed`, with the reason first in `notes`
        — the values stay in the row for inspection, but nothing downstream
        should mistake them for facts.
        """
        if not reported:
            return cls(
                file=file,
                source="failed",
                task_id=task_id,
                notes="the agent never reported",
            )
        fields = {name: reported.get(name) or None for name in FIELDS}
        confidence = float(reported.get("confidence") or 0.0)  # type: ignore[arg-type]
        notes = str(reported.get("notes") or "")
        missing = [name for name in FIELDS if not fields[name]]
        if reported.get("source") == "human":
            source = "human"
        elif missing:
            source, notes = "failed", _why(f"missing {', '.join(missing)}", notes)
        elif confidence < min_confidence:
            source, notes = (
                "failed",
                _why(
                    f"confidence {confidence:.2f} is below the "
                    f"{min_confidence:.2f} gate",
                    notes,
                ),
            )
        else:
            source = "model"
        return cls(
            file=file,
            source=source,
            fields=fields,
            confidence=confidence,
            price=price,
            task_id=task_id,
            notes=notes,
        )

    def as_row(self) -> dict[str, object]:
        """Flatten to CSV columns."""
        return {
            "file": self.file,
            **{name: self.fields.get(name) or "" for name in FIELDS},
            "source": self.source,
            "confidence": f"{self.confidence:.2f}",
            "price": self.price or "",
            "task_id": self.task_id or "",
            "notes": self.notes,
        }


def read_csv(path: Path) -> list[Record]:
    """Read a CSV written by `write_csv` back into records.

    Lets a restarted `--watch` keep the rows it already has instead of
    truncating the file down to whatever this run happens to touch.
    """
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Record(
                file=row.get("file", ""),
                source=row.get("source", "failed"),
                fields={name: row.get(name) or None for name in FIELDS},
                confidence=_as_float(row.get("confidence")),
                price=row.get("price") or None,
                task_id=row.get("task_id") or None,
                notes=row.get("notes", ""),
            )
            for row in csv.DictReader(handle)
            if row.get("file")
        ]


def _as_float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def write_csv(records: list[Record], path: Path) -> None:
    """Write the batch to `path`, creating parent folders as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(record.as_row())


class Sink:
    """Accumulates rows and rewrites the CSV whenever they change.

    Keyed by filename, so a placeholder row written before a human hand-off is
    replaced by the real one when it lands — and a restart can start from the
    rows already on disk. Feed it one record at a time and every settled
    document is in the file the moment it settles, however long the rest of
    the batch keeps waiting.
    """

    def __init__(self, out: Path, *, resume: bool = False) -> None:
        self.out = out
        self.rows: dict[str, Record] = (
            {record.file: record for record in read_csv(out)} if resume else {}
        )

    def settled(self) -> set[str]:
        """Filenames already finished — nothing left to do for them."""
        return {
            name
            for name, record in self.rows.items()
            if record.source in SETTLED_SOURCES
        }

    def update(self, records: list[Record]) -> None:
        for record in records:
            self.rows[record.file] = record
        ordered = sorted(self.rows.values(), key=lambda record: record.file)
        write_csv(ordered, self.out)
        log.info("%s → %s", summarize(ordered), self.out)


def failure_reason(exc: BaseException) -> str:
    """The first real message inside a (possibly nested) exception group."""
    inner = getattr(exc, "exceptions", None)
    if inner:
        return failure_reason(inner[0])
    text = str(exc).split("\n")[0].strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def summarize(records: list[Record]) -> str:
    """One line for the operator — a snapshot of the whole CSV."""
    count = Counter(record.source for record in records)
    spent = sum(_amount(record.price) for record in records)
    parts = [
        f"{count['model']} read by the model",
        f"{count['human']} by a human expert (${spent:.2f})",
    ]
    if count["processing"]:
        parts.append(f"{count['processing']} in processing")
    if count["failed"]:
        parts.append(f"{count['failed']} failed")
    return f"{len(records)} documents: {', '.join(parts)}"


def _why(reason: str, notes: str) -> str:
    """The failure reason first; whatever the model said after it."""
    return f"{reason}; {notes}" if notes else reason


def _amount(price: str | None) -> float:
    try:
        return float((price or "").lstrip("$").replace(",", ""))
    except ValueError:
        return 0.0
