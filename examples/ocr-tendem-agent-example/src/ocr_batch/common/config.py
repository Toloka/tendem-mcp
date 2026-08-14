"""Everything configurable, read once from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

#: The fields we pull out of every document. Change this list and the prompt,
#: the human brief and the CSV columns all follow.
FIELDS = (
    "invoice_number",
    "issue_date",
    "vendor_name",
    "total_amount",
    "currency",
)


@dataclass(frozen=True)
class Settings:
    """Credentials and knobs for one run."""

    base_url: str
    api_key: str
    ocr_model: str
    tendem_api_key: str
    max_price: float
    #: A model-only row must clear this. Clean scans self-report ~0.99 and
    #: guessed ones ~0.78-0.86, so the gate sits above the guesses.
    min_confidence: float = 0.95

    @classmethod
    def from_env(cls) -> Settings:
        """Load `.env` (if present) and read the settings out of the env."""
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            msg = (
                "OPENAI_API_KEY is not set. Point OPENAI_BASE_URL / "
                "OPENAI_API_KEY at any OpenAI-compatible endpoint — see "
                ".env.example."
            )
            raise SystemExit(msg)
        tendem_api_key = os.getenv("TENDEM_API_KEY")
        if not tendem_api_key:
            msg = (
                "TENDEM_API_KEY is not set. The human expert fallback is the "
                "point of this pipeline — get a key at agent.tendem.ai/mcp "
                '("Agent builders" tab), see .env.example.'
            )
            raise SystemExit(msg)
        return cls(
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=api_key,
            # One model for everything: it reads the scans and drives the
            # tools, so it needs vision *and* tool calling.
            ocr_model=os.getenv("OCR_MODEL", "gpt-4o-mini"),
            # `TENDEM_MCP_URL` is deliberately absent: langchain-tendem reads
            # it itself, so setting it in the environment moves the whole
            # pipeline to prestable with no code here.
            tendem_api_key=tendem_api_key,
            max_price=float(os.getenv("TENDEM_MAX_PRICE", "10")),
            min_confidence=float(os.getenv("OCR_MIN_CONFIDENCE", "0.95")),
        )
