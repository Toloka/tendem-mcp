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
    """List all Tendem tasks with their statuses.

    Args:
        page_number: Page number (0-indexed).
        page_size: Number of results per page (1-100).

    Returns:
        Paginated list of Tendem tasks.
    """
    return await get_client().list_tasks(page_number, page_size)


@mcp.tool
async def create_task(text: str) -> McpTaskView:
    """Create a new Tendem task for a human expert.

    Poll get_task until AWAITING_APPROVAL to see the price.

    After creation, poll with get_task until status is AWAITING_APPROVAL to see the price
    (may take up to 10 minutes).

    When creating a task consider available human expert specializations:
    **1. data_scraping**
    * Task types: High-volume data extraction, scraping, cleansing and
    enrichment (web, social media, or document sets).
    * Threshold: MUST be used when manual effort would exceed ~4 hours
    OR when tools like Selenium/BS4/Apify are required.
    **2. software_development**
    * Task types: Debugging/refactoring existing code. Writing automation
    scripts. Building full-stack apps (Python, Node, TS).
    Building/adding features to WordPress, Woocommerce, Shopify based
    websites and stores.
    **3. design**
    * Task types: Logos, Branding, Presentations (decks), Print Materials
    (flyers/brochures/billboards), and Packaging.
    **4. copywriting**
    * Definition: Writing where style, tone, and usage of language is
    important.
    * Task types: SEO Writing, Newsletters, Press Releases, Case Studies,
    Ad Copy, Landing Page texts, Social Media posts, UX Writing, Email
    campaigns, Proofreading, editing, refining and humanizing ai text.
    **5. general**
    * Definition: Expert level knowledge not required, good at attention
    to detail, using software and ai tools.
    * Task types: Manual data collection, enrichment, cleaning and
    analysis (incl. lead generation, contact list building), market
    research, formatting documents, converting files.

    Avoid tasks that:
    **Required Regulated Expertise:** Medical diagnosis, legal advice,
    PhD-level research, or real-money investment advice.
    **Required access to private/internal systems without providing
    credentials** e.g., "Check my email"

    Args:
        text: The task description/prompt to execute.

    Returns:
        The created task.
    """
    return await get_client().create_task(text)


@mcp.tool
async def get_task(task_id: str) -> McpTaskView:
    """Get Tendem task status and details. Use to poll after create_task or approve_task.

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
    """Approve a Tendem task and its price. A human expert will begin working (may take hours).

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
    """Cancel a Tendem task. Costs are not refunded after approval.

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
    """Get the final result text from a completed Tendem task.

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
    """Get all Tendem task results including intermediate drafts, from latest to oldest.

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
    """Download a file artifact (image, document) from Tendem task results and save locally.

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
