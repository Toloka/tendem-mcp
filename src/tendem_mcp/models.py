"""Pydantic models for Tendem MCP API."""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class McpTaskStatus(StrEnum):
    """Task status for MCP API."""

    DRAFT = 'DRAFT'
    AWAITING_APPROVAL = 'AWAITING_APPROVAL'
    PROCESSING = 'PROCESSING'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    FAILED = 'FAILED'


class McpApprovalRequestInfo(BaseModel):
    """Approval request information for task approval."""

    price_usd: Decimal
    created_at: datetime


class McpTaskView(BaseModel):
    """Unified task representation for MCP API.

    Used for task creation, status queries, and task listing.
    """

    task_id: UUID
    name: str
    status: McpTaskStatus
    created_at: datetime
    approval_request_info: McpApprovalRequestInfo | None = None


class McpTaskListView(BaseModel):
    """Paginated list of tasks."""

    tasks: Sequence[McpTaskView]
    total: int
    page_number: int
    page_size: int
    pages: int


class McpCanvasView(BaseModel):
    """Canvas (result) view."""

    canvas_id: UUID
    version_id: UUID
    created_at: datetime
    content: str


class McpTaskResultsView(BaseModel):
    """Paginated task results response."""

    canvases: Sequence[McpCanvasView]
    total: int
    page_number: int
    page_size: int
    pages: int


class McpCanvasToolResult(BaseModel):
    """Single canvas result for tool response (without internal IDs)."""

    created_at: datetime
    content: str


class McpAllTaskResultsToolResult(BaseModel):
    """Paginated results for get_all_task_results tool."""

    results: Sequence[McpCanvasToolResult]
    total: int
    page_number: int
    page_size: int
    pages: int
