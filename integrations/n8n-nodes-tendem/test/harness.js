'use strict';

// Shared test doubles. No tests live here.

/** Renders a JSON-RPC message the way an MCP server would, as JSON or as SSE. */
function encode(message, sse) {
	if (!sse) return { contentType: 'application/json', body: message };
	return {
		contentType: 'text/event-stream',
		body: `event: message\ndata: ${JSON.stringify(message)}\n\n`,
	};
}

function rpcResponse(statusCode, message, extraHeaders, sse) {
	const { contentType, body } = encode(message, sse);
	return {
		statusCode,
		headers: Object.assign({ 'content-type': contentType }, extraHeaders || {}),
		body,
	};
}

/**
 * A fake Tendem MCP endpoint. Handles the initialize handshake, then routes `tools/call` to
 * `toolHandler({ name, args, callIndex })`, which returns the JSON payload Tendem would put in a
 * text content block.
 */
function mockMcpServer(options) {
	const opts = options || {};
	const sessionId = 'sessionId' in opts ? opts.sessionId : 'sess-abc123';
	const sse = opts.sse === true;
	const protocolVersion = opts.protocolVersion || '2025-06-18';
	const toolHandler = opts.toolHandler || (() => ({ ok: true }));

	const httpCalls = [];
	const toolCalls = [];
	let initializeCount = 0;

	async function requester(requestOptions) {
		httpCalls.push(requestOptions);
		const body = requestOptions.body;

		if (body.method === 'initialize') {
			initializeCount += 1;
			return rpcResponse(
				200,
				{
					jsonrpc: '2.0',
					id: body.id,
					result: {
						protocolVersion,
						capabilities: { tools: {} },
						serverInfo: { name: 'tendem', version: '1.0.0' },
					},
				},
				sessionId === null ? {} : { 'mcp-session-id': sessionId },
				sse,
			);
		}

		if (body.method === 'notifications/initialized') {
			return { statusCode: 202, headers: {}, body: '' };
		}

		if (body.method === 'tools/call') {
			const name = body.params.name;
			const args = body.params.arguments;
			const callIndex = toolCalls.length;
			toolCalls.push({ name, args });
			const payload = await toolHandler({ name, args, callIndex });
			if (payload && payload.__httpStatus) {
				return rpcResponse(payload.__httpStatus, payload.__message || {}, {}, sse);
			}
			if (payload && payload.__isError) {
				return rpcResponse(
					200,
					{
						jsonrpc: '2.0',
						id: body.id,
						result: {
							isError: true,
							content: [{ type: 'text', text: payload.__isError }],
						},
					},
					{},
					sse,
				);
			}
			return rpcResponse(
				200,
				{
					jsonrpc: '2.0',
					id: body.id,
					result: { content: [{ type: 'text', text: JSON.stringify(payload) }] },
				},
				{},
				sse,
			);
		}

		throw new Error(`mock server got unexpected method: ${body.method}`);
	}

	return {
		requester,
		httpCalls,
		toolCalls,
		names: () => toolCalls.map((c) => c.name),
		countOf: (name) => toolCalls.filter((c) => c.name === name).length,
		initializeCount: () => initializeCount,
	};
}

/** Minimal IExecuteFunctions stand-in, enough to drive Tendem.execute(). */
function makeExecuteContext(options) {
	const opts = options || {};
	const params = opts.params || {};
	const items = opts.items || [{ json: {} }];
	const credentials = Object.assign(
		{ apiKey: 'test-key', endpoint: 'https://mcp.tendem.ai/mcp?utm_hash=83dad40a52' },
		opts.credentials || {},
	);
	const requester = opts.requester;
	const continueOnFail = opts.continueOnFail === true;

	const node = {
		id: 'node-1',
		name: 'Tendem',
		type: 'n8n-nodes-tendem.tendem',
		typeVersion: 1,
		position: [0, 0],
		parameters: {},
	};

	return {
		getInputData: () => items,
		getNode: () => node,
		continueOnFail: () => continueOnFail,
		getCredentials: async () => credentials,
		getNodeParameter(name, itemIndex, fallback) {
			const perItem = Array.isArray(params) ? params[itemIndex] : params;
			if (perItem && Object.prototype.hasOwnProperty.call(perItem, name)) return perItem[name];
			if (arguments.length >= 3) return fallback;
			throw new Error(`test harness: parameter "${name}" not provided`);
		},
		helpers: {
			httpRequestWithAuthentication: async function (credentialType, requestOptions) {
				if (credentialType !== 'tendemApi') {
					throw new Error(`unexpected credential type: ${credentialType}`);
				}
				return await requester(requestOptions);
			},
			returnJsonArray: (data) =>
				(Array.isArray(data) ? data : [data]).map((json) => ({ json })),
			constructExecutionMetaData: (data, meta) =>
				data.map((entry) => Object.assign({}, entry, { pairedItem: meta.itemData })),
		},
	};
}

/** Records sleeps instead of performing them. */
function makeClock(options) {
	const opts = options || {};
	// How long each simulated request appears to take.
	const requestDurationMs = opts.requestDurationMs === undefined ? 30_000 : opts.requestDurationMs;
	const sleeps = [];
	let current = 0;

	return {
		sleeps,
		now: () => current,
		advanceForRequest: () => {
			current += requestDurationMs;
		},
		sleep: async (ms) => {
			sleeps.push(ms);
			current += ms;
		},
	};
}

module.exports = { mockMcpServer, makeExecuteContext, makeClock, rpcResponse };
