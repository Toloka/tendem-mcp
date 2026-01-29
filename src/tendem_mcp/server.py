"""Tendem MCP server."""

import os
from functools import cache
from pathlib import Path
from uuid import UUID

from fastmcp import FastMCP

from tendem_mcp.client import TendemClient
from tendem_mcp.models import (
    McpAllTaskResultsToolResult,
    McpCanvasToolResult,
    McpTaskListView,
    McpTaskStatus,
    McpTaskView,
)

mcp = FastMCP('tendem-mcp')

DEFAULT_API_URL = 'https://api.tendem.ai/api/v0'


@cache
def get_client() -> TendemClient:
    """Get or create the cached Tendem client."""
    api_key = os.environ.get('TENDEM_API_KEY')
    if not api_key:
        raise ValueError('TENDEM_API_KEY environment variable is required')
    base_url = os.environ.get('TENDEM_API_URL', DEFAULT_API_URL)
    debug = os.environ.get('TENDEM_DEBUG', '').lower() in ('1', 'true', 'yes')
    return TendemClient(api_key=api_key, base_url=base_url, debug=debug)


@mcp.tool
async def list_tasks(page_number: int, page_size: int) -> McpTaskListView:
    """List all Tendem tasks for the current user.

    Args:
        page_number: Page number (0-indexed).
        page_size: Number of results per page (1-100).

    Returns:
        Paginated list of Tendem tasks.
    """
    return await get_client().list_tasks(page_number, page_size)


@mcp.tool
async def create_task(text: str) -> McpTaskView:
    """Create a new Tendem task with the given text.

    After creation, poll with get_task until status is AWAITING_APPROVAL to see the price
    (may take up to 10 minutes).

    Args:
        text: The task description/prompt to execute.

    Returns:
        The created task.
    """
    return await get_client().create_task(text)


@mcp.tool
async def get_task(task_id: str) -> McpTaskView:
    """Get a Tendem task by ID.

    Use to poll task status. After create_task, wait for AWAITING_APPROVAL to see price.
    After approve_task, a human expert works on the task until COMPLETED (may take hours).

    Args:
        task_id: The Tendem task ID (UUID) to get.

    Returns:
        The Tendem task including status and approval info if awaiting approval.
    """
    return await get_client().get_task(UUID(task_id))


@mcp.tool
async def approve_task(task_id: str) -> str:
    """Approve a Tendem task that is awaiting approval.

    Call after reviewing the price in AWAITING_APPROVAL status. A human expert will then
    work on the task until it reaches COMPLETED status (may take hours).

    Args:
        task_id: The Tendem task ID (UUID) to approve.

    Returns:
        Confirmation message.
    """
    await get_client().approve_task(UUID(task_id))
    return f'Task {task_id} approved'


@mcp.tool
async def cancel_task(task_id: str) -> str:
    """Cancel a Tendem task.

    Can be called at any time. Note: costs are not refunded if cancelled after approval.

    Args:
        task_id: The Tendem task ID (UUID) to cancel.

    Returns:
        Confirmation message.
    """
    await get_client().cancel_task(UUID(task_id))
    return f'Task {task_id} cancelled'


@mcp.tool
async def get_task_result(task_id: str) -> str:
    """Get the latest result from a completed Tendem task.

    Args:
        task_id: The Tendem task ID (UUID).

    Returns:
        The content of the latest canvas, or an error if task is not completed.
    """
    client = get_client()
    task = await client.get_task(UUID(task_id))
    if task.status != McpTaskStatus.COMPLETED:
        return f'Error: Task is not completed (status: {task.status.value})'
    results = await client.get_task_results(UUID(task_id), page_number=0, page_size=1)
    if not results.canvases:
        return 'Error: No results found for completed task'
    return results.canvases[0].content


@mcp.tool
async def get_all_task_results(
    task_id: str,
    page_number: int,
    page_size: int,
) -> McpAllTaskResultsToolResult:
    """Get all results (including intermediate) for a Tendem task, from latest to oldest.

    Args:
        task_id: The Tendem task ID (UUID) to get results for.
        page_number: Page number (0-indexed).
        page_size: Number of results per page (1-100).

    Returns:
        Paginated Tendem task results with canvas content.
    """
    results = await get_client().get_task_results(UUID(task_id), page_number, page_size)
    return McpAllTaskResultsToolResult(
        results=[
            McpCanvasToolResult(created_at=c.created_at, content=c.content)
            for c in results.canvases
        ],
        total=results.total,
        page_number=results.page_number,
        page_size=results.page_size,
        pages=results.pages,
    )


@mcp.tool
async def download_artifact(task_id: str, artifact_id: str, path: str) -> str:
    """Download artifact content from a Tendem task and save to a file.

    Artifact references appear in canvas content as:
    ```agents-reference
    aba://<artifact_id>
    ```

    Args:
        task_id: The Tendem task ID (UUID).
        artifact_id: The artifact ID (UUID) from the agents-reference block.
        path: The file path where the artifact should be saved.

    Returns:
        Confirmation message with the saved file path and size.
    """
    content = await get_client().get_artifact(UUID(task_id), artifact_id)
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    _ = file_path.write_bytes(content)
    return f'Artifact saved to {file_path} ({len(content)} bytes)'
