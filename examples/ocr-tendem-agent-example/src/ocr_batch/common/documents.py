"""Finding documents in a folder and handing them to a vision model."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
SUPPORTED = {*IMAGE_TYPES, ".pdf"}


def discover(folder: Path) -> list[Path]:
    """Every supported document in `folder` itself, in a stable order.

    Deliberately not recursive: a document's identity is its name plus its
    bytes (see `document_sha`), and one flat folder is what keeps the name
    half unambiguous. Recursing would let two identical files in different
    subfolders silently share one task and one CSV row.
    """
    return sorted(
        path
        for path in folder.glob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )


def document_sha(path: Path) -> str:
    """Short identity for a document: its name *and* its bytes.

    Both halves earn their place. The bytes mean a re-scan under the same name is
    a new document, so a resumed task can never answer for a version it no longer
    holds. The name means two byte-identical copies in the folder are two
    documents — each gets its own task, instead of silently sharing one while it
    is live, splitting again once it finishes, and reporting a single charge
    against every copy.
    """
    fingerprint = path.name.encode("utf-8") + b"\0" + path.read_bytes()
    return hashlib.sha256(fingerprint).hexdigest()[:12]


def text_sha(text: str) -> str:
    """Same, for the brief: a re-scoped question must not adopt an old task."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def as_data_url(path: Path) -> str:
    """The document as a `data:` URL a vision model accepts.

    PDFs are rasterised (first page, 2x) so the same code path serves scans
    and photos alike.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        data, mime = _render_pdf(path), "image/png"
    else:
        data, mime = path.read_bytes(), IMAGE_TYPES[suffix]
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _render_pdf(path: Path) -> bytes:
    import io

    import pypdfium2

    pdf = pypdfium2.PdfDocument(path)
    try:
        image = pdf[0].render(scale=2).to_pil()
    finally:
        pdf.close()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()
