"""Tendem API client."""

import json
import logging
from collections.abc import Callable, Coroutine
from typing import Self, cast
from uuid import UUID

import httpx

from tendem_mcp.models import (
    McpTaskListView,
    McpTaskResultsView,
    McpTaskView,
)

logger = logging.getLogger(__name__)

type RequestHook = Callable[[httpx.Request], Coroutine[None, None, None]]
type ResponseHook = Callable[[httpx.Response], Coroutine[None, None, None]]


class TendemAPIError(Exception):
    """Exception raised when the Tendem API returns an error."""

    status_code: int
    error_type: str | None
    detail: str | None

    def __init__(
        self,
        message: str,
        status_code: int,
        error_type: str | None = None,
        detail: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type
        self.detail = detail


def _raise_for_status(response: httpx.Response) -> None:
    """Raise TendemAPIError with details from the response body if request failed."""
    if response.is_success:
        return

    error_type: str | None = None
    detail: str | None = None
    title: str | None = None

    try:
        error_body = cast(dict[str, object], response.json())
        error_type = str(error_body.get('type')) if 'type' in error_body else None
        detail = str(error_body.get('detail')) if 'detail' in error_body else None
        title = str(error_body.get('title')) if 'title' in error_body else None
    except (json.JSONDecodeError, TypeError):
        detail = response.text if response.text else None

    if detail:
        message = f'{response.status_code} {title or response.reason_phrase}: {detail}'
    elif title:
        message = f'{response.status_code} {title}'
    else:
        message = f'{response.status_code} {response.reason_phrase}'

    raise TendemAPIError(
        message=message,
        status_code=response.status_code,
        error_type=error_type,
        detail=detail,
    )


async def _log_request(request: httpx.Request) -> None:
    """Log outgoing request details."""
    logger.debug('Request: %s %s', request.method, request.url)
    if request.content:
        logger.debug('Request body: %s', request.content.decode())


async def _log_response(response: httpx.Response) -> None:
    """Log incoming response details."""
    _ = await response.aread()
    logger.debug('Response: %s %s', response.status_code, response.url)
    logger.debug('Response body: %s', response.text)


class TendemClient:
    """Async client for Tendem MCP API."""

    api_key: str
    base_url: str
    _client: httpx.AsyncClient

    def __init__(self, api_key: str, base_url: str, *, debug: bool = False) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')

        request_hooks: list[RequestHook] = []
        response_hooks: list[ResponseHook] = []
        if debug:
            request_hooks = [_log_request]
            response_hooks = [_log_response]

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={'Authorization': f'ApiKey {self.api_key}'},
            event_hooks={'request': request_hooks, 'response': response_hooks},
        )

    async def list_tasks(
        self,
        page_number: int = 0,
        page_size: int = 10,
    ) -> McpTaskListView:
        """List all tasks for the current user.

        Args:
            page_number: Page number (0-indexed).
            page_size: Number of results per page (1-100).

        Returns:
            Paginated list of tasks.
        """
        response = await self._client.get(
            '/tasks',
            params={'pageNumber': page_number, 'pageSize': page_size},
        )
        _raise_for_status(response)
        return McpTaskListView.model_validate(response.json())

    async def create_task(self, text: str) -> McpTaskView:
        """Create a new task with the given text.

        Args:
            text: The task description/prompt.

        Returns:
            The created task.
        """
        response = await self._client.post('/tasks', json={'text': text})
        _raise_for_status(response)
        return McpTaskView.model_validate(response.json())

    async def get_task(self, task_id: UUID) -> McpTaskView:
        """Get a task by ID.

        Args:
            task_id: The task ID to get.

        Returns:
            The task including status and approval info if awaiting approval.
        """
        response = await self._client.get(f'/tasks/{task_id}')
        _raise_for_status(response)
        return McpTaskView.model_validate(response.json())

    async def approve_task(self, task_id: UUID) -> None:
        """Approve the pending approval request for a task.

        Args:
            task_id: The task ID to approve.
        """
        response = await self._client.post(f'/tasks/{task_id}/approve')
        _raise_for_status(response)

    async def cancel_task(self, task_id: UUID) -> None:
        """Cancel a task.

        Args:
            task_id: The task ID to cancel.
        """
        response = await self._client.post(f'/tasks/{task_id}/cancel')
        _raise_for_status(response)

    async def get_task_results(
        self,
        task_id: UUID,
        page_number: int = 0,
        page_size: int = 10,
    ) -> McpTaskResultsView:
        """Get the results (published canvases) for a task.

        Args:
            task_id: The task ID to get results for.
            page_number: Page number (0-indexed).
            page_size: Number of results per page (1-100).

        Returns:
            Paginated task results.
        """
        response = await self._client.get(
            f'/tasks/{task_id}/results',
            params={'pageNumber': page_number, 'pageSize': page_size},
        )
        _raise_for_status(response)
        return McpTaskResultsView.model_validate(response.json())

    async def get_artifact(self, task_id: UUID, artifact_id: str) -> bytes:
        """Get artifact content by artifact ID.

        Args:
            task_id: The task ID.
            artifact_id: The artifact ID (UUID).

        Returns:
            The artifact content as bytes.
        """
        response = await self._client.get(f'/tasks/{task_id}/artifacts/{artifact_id}')
        _raise_for_status(response)
        return response.content

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> Self:
        """Enter async context manager."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit async context manager."""
        await self.close()
