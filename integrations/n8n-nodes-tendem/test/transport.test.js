'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
	MCP_PROTOCOL_VERSION,
	McpError,
	McpSession,
	TENDEM_DEFAULT_ENDPOINT,
	extractJsonRpcMessage,
	toolResultIsError,
	unwrapToolResult,
} = require('../dist/nodes/Tendem/transport.js');
const { mockMcpServer } = require('./harness.js');

const ENDPOINT = TENDEM_DEFAULT_ENDPOINT;

test('the default endpoint carries the n8n attribution hash', () => {
	assert.equal(TENDEM_DEFAULT_ENDPOINT, 'https://mcp.tendem.ai/mcp?utm_hash=83dad40a52');
});

test('extractJsonRpcMessage handles a plain JSON object body', () => {
	const message = { jsonrpc: '2.0', id: 1, result: { ok: true } };
	assert.deepEqual(extractJsonRpcMessage(message), message);
});

test('extractJsonRpcMessage handles a JSON string body', () => {
	const parsed = extractJsonRpcMessage('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}');
	assert.deepEqual(parsed.result, { ok: true });
});

test('extractJsonRpcMessage parses an SSE stream', () => {
	const sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n';
	assert.deepEqual(extractJsonRpcMessage(sse).result, { ok: true });
});

test('extractJsonRpcMessage skips SSE notifications and returns the response', () => {
	const sse = [
		': keep-alive comment',
		'event: message',
		'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}',
		'',
		'event: message',
		'data: {"jsonrpc":"2.0","id":7,"result":{"final":true}}',
		'',
	].join('\n');

	assert.deepEqual(extractJsonRpcMessage(sse).result, { final: true });
});

test('extractJsonRpcMessage handles CRLF and multi-line SSE data', () => {
	const sse = 'event: message\r\ndata: {"jsonrpc":"2.0","id":1,\r\ndata: "result":{"ok":1}}\r\n\r\n';
	assert.deepEqual(extractJsonRpcMessage(sse).result, { ok: 1 });
});

test('extractJsonRpcMessage returns undefined for empty or unusable bodies', () => {
	for (const body of ['', '   ', undefined, null, 'not json at all', 42]) {
		assert.equal(extractJsonRpcMessage(body), undefined);
	}
});

test('unwrapToolResult parses the JSON Tendem puts in a text block', () => {
	const result = {
		content: [{ type: 'text', text: JSON.stringify({ task_id: 'abc', status: 'ACTING' }) }],
	};
	assert.deepEqual(unwrapToolResult(result), { task_id: 'abc', status: 'ACTING' });
});

test('unwrapToolResult prefers structuredContent when present', () => {
	const result = {
		structuredContent: { task_id: 'from-structured' },
		content: [{ type: 'text', text: '{"task_id":"from-text"}' }],
	};
	assert.equal(unwrapToolResult(result).task_id, 'from-structured');
});

test('unwrapToolResult wraps a JSON array under items', () => {
	const result = { content: [{ type: 'text', text: '[{"task_id":"a"},{"task_id":"b"}]' }] };
	assert.deepEqual(unwrapToolResult(result), { items: [{ task_id: 'a' }, { task_id: 'b' }] });
});

test('unwrapToolResult falls back to raw text when the payload is not JSON', () => {
	const result = { content: [{ type: 'text', text: 'Tendem says hello' }] };
	assert.deepEqual(unwrapToolResult(result), { text: 'Tendem says hello' });
});

test('unwrapToolResult joins multiple text blocks and ignores non-text parts', () => {
	const result = {
		content: [
			{ type: 'text', text: 'line one' },
			{ type: 'image', data: 'ignored' },
			{ type: 'text', text: 'line two' },
		],
	};
	assert.deepEqual(unwrapToolResult(result), { text: 'line one\nline two' });
});

test('toolResultIsError only flags an explicit isError', () => {
	assert.equal(toolResultIsError({ isError: true }), true);
	assert.equal(toolResultIsError({ isError: false }), false);
	assert.equal(toolResultIsError({}), false);
	assert.equal(toolResultIsError(null), false);
});

test('a tool call performs the initialize handshake exactly once', async () => {
	const server = mockMcpServer({ toolHandler: () => ({ balance: 12.5 }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await session.callTool('get_account', {});
	await session.callTool('get_account', {});

	assert.equal(server.initializeCount(), 1);

	const methods = server.httpCalls.map((c) => c.body.method);
	assert.deepEqual(methods, [
		'initialize',
		'notifications/initialized',
		'tools/call',
		'tools/call',
	]);
});

test('concurrent tool calls share a single handshake', async () => {
	const server = mockMcpServer({ toolHandler: () => ({ ok: true }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await Promise.all([
		session.callTool('get_account', {}),
		session.callTool('get_account', {}),
		session.callTool('get_account', {}),
	]);

	assert.equal(server.initializeCount(), 1);
});

test('the session id from initialize is replayed on later requests', async () => {
	const server = mockMcpServer({ sessionId: 'sess-xyz', toolHandler: () => ({ ok: true }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await session.callTool('get_account', {});

	const initialize = server.httpCalls[0];
	const toolCall = server.httpCalls.at(-1);

	assert.equal(initialize.headers['Mcp-Session-Id'], undefined);
	assert.equal(toolCall.headers['Mcp-Session-Id'], 'sess-xyz');
});

test('requests advertise both JSON and SSE, and send the protocol version after initialize', async () => {
	const server = mockMcpServer({ toolHandler: () => ({ ok: true }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await session.callTool('get_account', {});

	for (const call of server.httpCalls) {
		assert.equal(call.headers.Accept, 'application/json, text/event-stream');
		assert.equal(call.headers['Content-Type'], 'application/json');
		assert.equal(call.method, 'POST');
		assert.equal(call.url, ENDPOINT);
	}

	assert.equal(server.httpCalls[0].headers['MCP-Protocol-Version'], undefined);
	assert.equal(server.httpCalls.at(-1).headers['MCP-Protocol-Version'], MCP_PROTOCOL_VERSION);
});

test('the negotiated protocol version is echoed back on later requests', async () => {
	const server = mockMcpServer({
		protocolVersion: '2025-03-26',
		toolHandler: () => ({ ok: true }),
	});
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await session.callTool('get_account', {});

	assert.equal(server.httpCalls.at(-1).headers['MCP-Protocol-Version'], '2025-03-26');
});

test('the transport never sets an Authorization header itself', async () => {
	const server = mockMcpServer({ toolHandler: () => ({ ok: true }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await session.callTool('get_account', {});

	for (const call of server.httpCalls) {
		assert.equal(call.headers.Authorization, undefined);
		assert.equal(call.headers.authorization, undefined);
	}
});

test('an SSE-only server works end to end', async () => {
	const server = mockMcpServer({ sse: true, toolHandler: () => ({ balance: 7 }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	assert.deepEqual(await session.callTool('get_account', {}), { balance: 7 });
});

test('tool arguments reach the server untouched', async () => {
	const server = mockMcpServer({ toolHandler: () => ({ ok: true }) });
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await session.callTool('get_task', { task_id: 't-1', wait_for_change_seconds: 30 });

	assert.deepEqual(server.toolCalls[0], {
		name: 'get_task',
		args: { task_id: 't-1', wait_for_change_seconds: 30 },
	});
});

test('an HTTP 401 produces an actionable credential error', async () => {
	const requester = async () => ({
		statusCode: 401,
		headers: {},
		body: 'Unauthorized',
	});
	const session = new McpSession(requester, { endpoint: ENDPOINT });

	await assert.rejects(async () => await session.callTool('get_account', {}), (error) => {
		assert.ok(error instanceof McpError);
		assert.match(error.message, /HTTP 401/);
		assert.match(error.message, /agent\.tendem\.ai\/tokens/);
		return true;
	});
});

test('a JSON-RPC error is surfaced with its code', async () => {
	const requester = async (options) => ({
		statusCode: 200,
		headers: {},
		body: {
			jsonrpc: '2.0',
			id: options.body.id,
			error: { code: -32602, message: 'Invalid params' },
		},
	});
	const session = new McpSession(requester, { endpoint: ENDPOINT });

	await assert.rejects(async () => await session.callTool('get_task', {}), (error) => {
		assert.equal(error.code, -32602);
		assert.match(error.message, /Invalid params/);
		return true;
	});
});

test('a tool-level isError result throws with the Tendem message', async () => {
	const server = mockMcpServer({
		toolHandler: () => ({ __isError: 'Tool failed (TASK_NOT_FOUND): no such task' }),
	});
	const session = new McpSession(server.requester, { endpoint: ENDPOINT });

	await assert.rejects(async () => await session.callTool('get_task', { task_id: 'nope' }), (error) => {
		assert.match(error.message, /TASK_NOT_FOUND/);
		return true;
	});
});

test('an expired session is re-established and the call retried once', async () => {
	let initializes = 0;
	let toolAttempts = 0;
	const requester = async (options) => {
		const body = options.body;
		if (body.method === 'initialize') {
			initializes += 1;
			return {
				statusCode: 200,
				headers: { 'mcp-session-id': `sess-${initializes}` },
				body: { jsonrpc: '2.0', id: body.id, result: { protocolVersion: MCP_PROTOCOL_VERSION } },
			};
		}
		if (body.method === 'notifications/initialized') {
			return { statusCode: 202, headers: {}, body: '' };
		}
		toolAttempts += 1;
		if (toolAttempts === 1) {
			// Server dropped the session.
			return { statusCode: 404, headers: {}, body: 'Session not found' };
		}
		return {
			statusCode: 200,
			headers: {},
			body: {
				jsonrpc: '2.0',
				id: body.id,
				result: { content: [{ type: 'text', text: '{"recovered":true}' }] },
			},
		};
	};

	const session = new McpSession(requester, { endpoint: ENDPOINT });
	const result = await session.callTool('get_account', {});

	assert.deepEqual(result, { recovered: true });
	assert.equal(initializes, 2, 'should re-initialize after a 404');
	assert.equal(toolAttempts, 2, 'should retry exactly once');
});

test('a persistently expired session gives up instead of retrying forever', async () => {
	let toolAttempts = 0;
	const requester = async (options) => {
		const body = options.body;
		if (body.method === 'initialize') {
			return {
				statusCode: 200,
				headers: { 'mcp-session-id': 'sess-1' },
				body: { jsonrpc: '2.0', id: body.id, result: {} },
			};
		}
		if (body.method === 'notifications/initialized') {
			return { statusCode: 202, headers: {}, body: '' };
		}
		toolAttempts += 1;
		return { statusCode: 404, headers: {}, body: 'gone' };
	};

	const session = new McpSession(requester, { endpoint: ENDPOINT });
	await assert.rejects(async () => await session.callTool('get_account', {}));
	assert.equal(toolAttempts, 2, 'at most one retry');
});

test('a failed handshake does not poison the session permanently', async () => {
	let failNext = true;
	const server = mockMcpServer({ toolHandler: () => ({ ok: true }) });
	const requester = async (options) => {
		if (options.body.method === 'initialize' && failNext) {
			failNext = false;
			return { statusCode: 503, headers: {}, body: 'Service Unavailable' };
		}
		return await server.requester(options);
	};

	const session = new McpSession(requester, { endpoint: ENDPOINT });

	await assert.rejects(async () => await session.callTool('get_account', {}));
	// A later call retries the handshake rather than reusing the rejected promise.
	assert.deepEqual(await session.callTool('get_account', {}), { ok: true });
});

test('an empty successful body is reported rather than silently returning nothing', async () => {
	const requester = async () => ({ statusCode: 200, headers: {}, body: '' });
	const session = new McpSession(requester, { endpoint: ENDPOINT });

	await assert.rejects(async () => await session.callTool('get_account', {}), /empty response/);
});
