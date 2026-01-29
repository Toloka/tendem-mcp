"""Tests for TendemClient."""

from decimal import Decimal
from uuid import UUID

import httpx
import pytest
import respx

from tendem_mcp.client import TendemAPIError, TendemClient
from tendem_mcp.models import McpTaskStatus

BASE_URL = 'https://api.tendem.ai'
API_KEY = 'test-api-key'

TASK_ID = UUID('12345678-1234-5678-1234-567812345678')
TASK_ID_2 = UUID('22222222-2222-2222-2222-222222222222')
CANVAS_ID = UUID('abcdefab-abcd-abcd-abcd-abcdefabcdef')
VERSION_ID = UUID('11111111-2222-3333-4444-555555555555')
ARTIFACT_ID = 'artifact-123'


@pytest.fixture
def client() -> TendemClient:
    """Create a TendemClient instance for testing."""
    return TendemClient(api_key=API_KEY, base_url=BASE_URL)


def _task_response(
    task_id: UUID = TASK_ID,
    name: str = 'Test task',
    status: str = 'PROCESSING',
    created_at: str = '2024-01-15T10:30:00Z',
    approval_request_info: dict[str, str] | None = None,
) -> dict[str, object]:
    """Create a task response dict."""
    return {
        'task_id': str(task_id),
        'name': name,
        'status': status,
        'created_at': created_at,
        'approval_request_info': approval_request_info,
    }


@pytest.mark.asyncio
@respx.mock
async def test_list_tasks(client: TendemClient) -> None:
    """Test listing tasks."""
    response_data = {
        'tasks': [
            _task_response(TASK_ID, 'Task 1'),
            _task_response(TASK_ID_2, 'Task 2', 'COMPLETED'),
        ],
        'total': 2,
        'page_number': 0,
        'page_size': 10,
        'pages': 1,
    }
    route = respx.get(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(200, json=response_data),
    )

    result = await client.list_tasks(page_number=0, page_size=10)

    assert route.called
    assert result.total == 2
    assert result.page_number == 0
    assert result.page_size == 10
    assert result.pages == 1
    assert len(result.tasks) == 2
    assert result.tasks[0].task_id == TASK_ID
    assert result.tasks[0].name == 'Task 1'
    assert result.tasks[1].task_id == TASK_ID_2
    assert result.tasks[1].status == McpTaskStatus.COMPLETED


@pytest.mark.asyncio
@respx.mock
async def test_list_tasks_pagination(client: TendemClient) -> None:
    """Test list_tasks sends correct pagination parameters."""
    response_data: dict[str, list[object] | int] = {
        'tasks': [],
        'total': 0,
        'page_number': 2,
        'page_size': 5,
        'pages': 0,
    }
    route = respx.get(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(200, json=response_data),
    )

    _ = await client.list_tasks(page_number=2, page_size=5)

    request = route.calls.last.request
    assert 'pageNumber=2' in str(request.url)
    assert 'pageSize=5' in str(request.url)


@pytest.mark.asyncio
@respx.mock
async def test_create_task(client: TendemClient) -> None:
    """Test creating a task."""
    response_data = _task_response()
    _ = respx.post(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(201, json=response_data),
    )

    result = await client.create_task('Test task description')

    assert result.task_id == TASK_ID
    assert result.name == 'Test task'
    assert result.status == McpTaskStatus.PROCESSING
    assert result.created_at.year == 2024


@pytest.mark.asyncio
@respx.mock
async def test_create_task_sends_correct_payload(client: TendemClient) -> None:
    """Test that create_task sends the correct JSON payload."""
    response_data = _task_response()
    route = respx.post(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(201, json=response_data),
    )

    _ = await client.create_task('My task text')

    assert route.called
    request = route.calls.last.request
    assert request.content == b'{"text":"My task text"}'


@pytest.mark.asyncio
@respx.mock
async def test_create_task_includes_auth_header(client: TendemClient) -> None:
    """Test that requests include the Authorization header."""
    response_data = _task_response()
    route = respx.post(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(201, json=response_data),
    )

    _ = await client.create_task('Test')

    request = route.calls.last.request
    assert request.headers['Authorization'] == f'ApiKey {API_KEY}'


@pytest.mark.asyncio
@respx.mock
async def test_get_task_processing(client: TendemClient) -> None:
    """Test getting task when processing."""
    response_data = _task_response(status='PROCESSING')
    _ = respx.get(f'{BASE_URL}/tasks/{TASK_ID}').mock(
        return_value=httpx.Response(200, json=response_data),
    )

    result = await client.get_task(TASK_ID)

    assert result.task_id == TASK_ID
    assert result.status == McpTaskStatus.PROCESSING
    assert result.approval_request_info is None


@pytest.mark.asyncio
@respx.mock
async def test_get_task_awaiting_approval(client: TendemClient) -> None:
    """Test getting task when awaiting approval."""
    response_data = _task_response(
        status='AWAITING_APPROVAL',
        approval_request_info={
            'price_usd': '10.50',
            'created_at': '2024-01-15T10:30:00Z',
        },
    )
    _ = respx.get(f'{BASE_URL}/tasks/{TASK_ID}').mock(
        return_value=httpx.Response(200, json=response_data),
    )

    result = await client.get_task(TASK_ID)

    assert result.task_id == TASK_ID
    assert result.status == McpTaskStatus.AWAITING_APPROVAL
    assert result.approval_request_info is not None
    assert result.approval_request_info.price_usd == Decimal('10.50')


@pytest.mark.asyncio
@respx.mock
async def test_approve_task(client: TendemClient) -> None:
    """Test approving a task."""
    route = respx.post(f'{BASE_URL}/tasks/{TASK_ID}/approve').mock(
        return_value=httpx.Response(204),
    )

    await client.approve_task(TASK_ID)

    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_cancel_task(client: TendemClient) -> None:
    """Test cancelling a task."""
    route = respx.post(f'{BASE_URL}/tasks/{TASK_ID}/cancel').mock(
        return_value=httpx.Response(204),
    )

    await client.cancel_task(TASK_ID)

    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_get_task_results(client: TendemClient) -> None:
    """Test getting task results."""
    response_data = {
        'canvases': [
            {
                'canvas_id': str(CANVAS_ID),
                'version_id': str(VERSION_ID),
                'created_at': '2024-01-15T10:30:00Z',
                'content': '# Result content',
            },
        ],
        'total': 1,
        'page_number': 0,
        'page_size': 10,
        'pages': 1,
    }
    _ = respx.get(f'{BASE_URL}/tasks/{TASK_ID}/results').mock(
        return_value=httpx.Response(200, json=response_data),
    )

    result = await client.get_task_results(TASK_ID)

    assert result.total == 1
    assert result.page_number == 0
    assert result.page_size == 10
    assert result.pages == 1
    assert len(result.canvases) == 1
    assert result.canvases[0].canvas_id == CANVAS_ID
    assert result.canvases[0].content == '# Result content'


@pytest.mark.asyncio
@respx.mock
async def test_get_task_results_with_pagination(client: TendemClient) -> None:
    """Test getting task results with pagination parameters."""
    response_data: dict[str, list[dict[str, str]] | int] = {
        'canvases': [],
        'total': 25,
        'page_number': 2,
        'page_size': 5,
        'pages': 5,
    }
    route = respx.get(f'{BASE_URL}/tasks/{TASK_ID}/results').mock(
        return_value=httpx.Response(200, json=response_data),
    )

    result = await client.get_task_results(TASK_ID, page_number=2, page_size=5)

    assert result.page_number == 2
    assert result.page_size == 5
    request = route.calls.last.request
    assert 'pageNumber=2' in str(request.url)
    assert 'pageSize=5' in str(request.url)


@pytest.mark.asyncio
@respx.mock
async def test_get_artifact(client: TendemClient) -> None:
    """Test getting an artifact."""
    artifact_content = b'binary artifact content'
    _ = respx.get(f'{BASE_URL}/tasks/{TASK_ID}/artifacts/{ARTIFACT_ID}').mock(
        return_value=httpx.Response(200, content=artifact_content),
    )

    result = await client.get_artifact(TASK_ID, ARTIFACT_ID)

    assert result == artifact_content


@pytest.mark.asyncio
@respx.mock
async def test_http_error_raises_exception(client: TendemClient) -> None:
    """Test that HTTP errors raise exceptions with details."""
    _ = respx.post(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(
            401,
            json={
                'type': '/auth/unauthorized',
                'title': 'Unauthorized',
                'detail': 'Invalid API key',
            },
        ),
    )

    with pytest.raises(TendemAPIError) as exc_info:
        _ = await client.create_task('Test')

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == 'Invalid API key'
    assert exc_info.value.error_type == '/auth/unauthorized'


@pytest.mark.asyncio
@respx.mock
async def test_server_error_raises_exception(client: TendemClient) -> None:
    """Test that server errors raise exceptions with details."""
    _ = respx.get(f'{BASE_URL}/tasks/{TASK_ID}').mock(
        return_value=httpx.Response(
            500,
            json={
                'type': '/server/error',
                'title': 'Internal Server Error',
                'detail': 'Database unavailable',
            },
        ),
    )

    with pytest.raises(TendemAPIError) as exc_info:
        _ = await client.get_task(TASK_ID)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == 'Database unavailable'


@pytest.mark.asyncio
@respx.mock
async def test_eula_not_accepted_error(client: TendemClient) -> None:
    """Test that EULA not accepted error is returned with details."""
    _ = respx.post(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(
            409,
            json={
                'type': '/user/eula_not_accepted',
                'title': 'Conflict',
                'status': 409,
                'detail': 'EULA version 1 is required',
            },
        ),
    )

    with pytest.raises(TendemAPIError) as exc_info:
        _ = await client.create_task('Test')

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_type == '/user/eula_not_accepted'
    assert exc_info.value.detail == 'EULA version 1 is required'
    assert 'EULA version 1 is required' in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_context_manager() -> None:
    """Test using client as async context manager."""
    response_data = _task_response()
    _ = respx.post(f'{BASE_URL}/tasks').mock(
        return_value=httpx.Response(201, json=response_data),
    )

    async with TendemClient(api_key=API_KEY, base_url=BASE_URL) as ctx_client:
        result = await ctx_client.create_task('Test')
        assert result.task_id == TASK_ID


@pytest.mark.asyncio
async def test_base_url_trailing_slash_stripped() -> None:
    """Test that trailing slash in base_url is stripped."""
    test_client = TendemClient(api_key=API_KEY, base_url='https://api.example.com/')
    assert test_client.base_url == 'https://api.example.com'
    await test_client.close()
