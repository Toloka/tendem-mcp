"""The one model, on whatever OpenAI-compatible endpoint was configured.

Both flows share one instance: in the agentic flow it reads the scans *and*
drives the tools, in the scripted flow it reads the scans and answers
Tendem's scoping questions. It also carries the batch's only throttle — the
LLM endpoint is the one thing a folder of documents can overload. Nothing
else needs one: waiting on a human is server-side and free, however many
documents wait at once.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from langchain_openai import ChatOpenAI
from pydantic import PrivateAttr

from ocr_batch.common.config import Settings


class CappedChatOpenAI(ChatOpenAI):
    """`ChatOpenAI` with a cap on concurrent model calls.

    A semaphore around each request: the slot is held for the duration of one
    call and released between calls, so an agent blocked on a human expert
    holds nothing. LangChain's built-in `rate_limiter` limits requests per
    second; this caps requests in flight, which is what `--concurrency`
    means here.
    """

    max_concurrency: int = 4
    _gate: asyncio.Semaphore | None = PrivateAttr(default=None)

    @property
    def gate(self) -> asyncio.Semaphore:
        if self._gate is None:
            self._gate = asyncio.Semaphore(self.max_concurrency)
        return self._gate

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        async with self.gate:
            return await super()._agenerate(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        async with self.gate:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk


def build_llm(settings: Settings, *, max_concurrency: int = 4) -> ChatOpenAI:
    """A chat model pointed at `OPENAI_BASE_URL` / `OCR_MODEL`."""
    return CappedChatOpenAI(
        model=settings.ocr_model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=0,
        max_retries=3,
        max_concurrency=max_concurrency,
    )
