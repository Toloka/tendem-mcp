'use strict';

// End-to-end tests through Tendem.execute(). The other suites test the guard, the wait loop and the
// transport in isolation; this one checks the assembled node — in particular that the only way to
// reach approve_task is the Approve operation with Confirm Spend deliberately turned on.

const test = require('node:test');
const assert = require('node:assert/strict');

const { Tendem } = require('../dist/nodes/Tendem/Tendem.node.js');
const { TENDEM_TOOLS } = require('../dist/nodes/Tendem/tools.js');
const { mockMcpServer, makeExecuteContext } = require('./harness.js');

const TASK_ID = '11111111-2222-4333-8444-555555555555';

/** Drives the real node against a mock Tendem MCP server. */
async function execute(params, options) {
	const opts = options || {};
	const server = mockMcpServer({ toolHandler: opts.toolHandler });
	const context = makeExecuteContext({
		params,
		items: opts.items,
		requester: server.requester,
		continueOnFail: opts.continueOnFail,
	});
	const output = await Tendem.prototype.execute.call(context);
	return { output: output[0], server };
}

/** Every operation the node exposes, with the parameters each one needs. */
const EVERY_OPERATION = [
	{ resource: 'task', operation: 'create', taskName: 'Research', description: 'Do the thing' },
	{ resource: 'task', operation: 'get', taskId: TASK_ID },
	{ resource: 'task', operation: 'getContract', taskId: TASK_ID },
	{ resource: 'task', operation: 'cancel', taskId: TASK_ID, taskName: 'Research' },
	{ resource: 'task', operation: 'getResult', taskId: TASK_ID },
	{ resource: 'task', operation: 'list', limit: 50, offset: 0 },
	{ resource: 'task', operation: 'wait', taskId: TASK_ID, waitForChangeSeconds: 30, maxRounds: 3 },
	{ resource: 'chat', operation: 'read', taskId: TASK_ID, fromOffset: 0 },
	{ resource: 'chat', operation: 'send', taskId: TASK_ID, text: 'hello', lastSeenOffset: 0 },
	{ resource: 'account', operation: 'get' },
	{ resource: 'file', operation: 'getUploadUrl', taskId: TASK_ID },
];

const APPROVED = { approved: true, task_id: TASK_ID, next_action: 'awaiting_tendem_work' };

test('the node reaches the Tendem endpoint carrying the attribution hash', async () => {
	const { server } = await execute({ resource: 'account', operation: 'get' });

	assert.ok(server.httpCalls.length > 0);
	for (const call of server.httpCalls) {
		assert.equal(call.url, 'https://mcp.tendem.ai/mcp?utm_hash=83dad40a52');
	}
});

test('a credential endpoint override is honoured', async () => {
	const server = mockMcpServer({});
	const context = makeExecuteContext({
		params: { resource: 'account', operation: 'get' },
		requester: server.requester,
		credentials: { endpoint: 'https://staging.example.test/mcp' },
	});

	await Tendem.prototype.execute.call(context);

	for (const call of server.httpCalls) {
		assert.equal(call.url, 'https://staging.example.test/mcp');
	}
});

test('a blank credential endpoint falls back to the default', async () => {
	const server = mockMcpServer({});
	const context = makeExecuteContext({
		params: { resource: 'account', operation: 'get' },
		requester: server.requester,
		credentials: { endpoint: '   ' },
	});

	await Tendem.prototype.execute.call(context);

	for (const call of server.httpCalls) {
		assert.equal(call.url, 'https://mcp.tendem.ai/mcp?utm_hash=83dad40a52');
	}
});

test('no operation other than Approve ever puts approve_task on the wire', async () => {
	for (const params of EVERY_OPERATION) {
		const { server } = await execute(params, {
			toolHandler: ({ name }) => {
				// get_task answers "done" so the wait operation settles on its first round.
				if (name === TENDEM_TOOLS.GET_TASK) return { status: 'CLOSED', next_action: 'done' };
				return { ok: true };
			},
		});

		assert.equal(
			server.countOf(TENDEM_TOOLS.APPROVE_TASK),
			0,
			`${params.resource}:${params.operation} issued approve_task`,
		);
	}
});

test('creating a task does not approve it, price it, or poll it', async () => {
	const { server } = await execute({
		resource: 'task',
		operation: 'create',
		taskName: 'Research',
		description: 'Do the thing',
	});

	assert.deepEqual(server.names(), [TENDEM_TOOLS.CREATE_TASK]);
});

test('a task that is ready for approval is still not approved by Get or Wait', async () => {
	// The strongest form of the accident this guards against: Tendem says "ready_for_approval" and
	// hands over a price, and the node must still do nothing about it.
	const ready = {
		task_id: TASK_ID,
		status: 'LISTENING',
		ready_for_approval: true,
		price: '$40.00',
		next_action: 'await_user_approval',
	};

	for (const params of [
		{ resource: 'task', operation: 'get', taskId: TASK_ID },
		{ resource: 'task', operation: 'wait', taskId: TASK_ID, maxRounds: 3 },
	]) {
		const { output, server } = await execute(params, { toolHandler: () => ready });

		assert.equal(server.countOf(TENDEM_TOOLS.APPROVE_TASK), 0);
		assert.equal(output[0].json.ready_for_approval, true);
		assert.equal(output[0].json.price, '$40.00');
	}
});

test('Approve refuses while Confirm Spend is off, without touching the network', async () => {
	await assert.rejects(
		async () =>
			await execute({
				resource: 'task',
				operation: 'approve',
				taskId: TASK_ID,
				taskName: 'Research',
				price: '$40.00',
				confirmSpend: false,
			}),
		(error) => {
			assert.match(error.message, /not confirmed/i);
			return true;
		},
	);
});

test('Approve refuses without a price, so the amount must have been read first', async () => {
	for (const price of ['', '   ']) {
		await assert.rejects(
			async () =>
				await execute({
					resource: 'task',
					operation: 'approve',
					taskId: TASK_ID,
					taskName: 'Research',
					price,
					confirmSpend: true,
				}),
			(error) => {
				assert.match(error.message, /no price was supplied/i);
				return true;
			},
		);
	}
});

test('a refused approval never reaches the Tendem server', async () => {
	const server = mockMcpServer({});
	const context = makeExecuteContext({
		params: {
			resource: 'task',
			operation: 'approve',
			taskId: TASK_ID,
			taskName: 'Research',
			price: '$40.00',
			confirmSpend: false,
		},
		requester: server.requester,
	});

	await assert.rejects(async () => await Tendem.prototype.execute.call(context));

	assert.equal(server.countOf(TENDEM_TOOLS.APPROVE_TASK), 0);
});

test('Approve spends exactly once when confirmed, passing the price through', async () => {
	const { output, server } = await execute(
		{
			resource: 'task',
			operation: 'approve',
			taskId: TASK_ID,
			taskName: 'Research',
			price: '$40.00',
			confirmSpend: true,
		},
		{ toolHandler: () => APPROVED },
	);

	assert.deepEqual(server.names(), [TENDEM_TOOLS.APPROVE_TASK]);
	assert.deepEqual(server.toolCalls[0].args, {
		task_id: TASK_ID,
		name: 'Research',
		price: '$40.00',
	});
	assert.equal(output[0].json.approved, true);
	assert.equal(output[0].json.spendBlocked, false);
	assert.equal(output[0].json.topupUrl, null);
});

test('insufficient balance comes back as routable data, and is never retried', async () => {
	const { output, server } = await execute(
		{
			resource: 'task',
			operation: 'approve',
			taskId: TASK_ID,
			taskName: 'Research',
			price: '$40.00',
			confirmSpend: true,
		},
		{
			toolHandler: () => ({
				approved: false,
				reason: 'insufficient_balance',
				topup_url: 'https://agent.tendem.ai/topup/abc',
				next_action: 'await_user_topup',
			}),
		},
	);

	assert.equal(server.countOf(TENDEM_TOOLS.APPROVE_TASK), 1);
	assert.equal(output[0].json.approved, false);
	assert.equal(output[0].json.spendBlocked, true);
	assert.equal(output[0].json.topupUrl, 'https://agent.tendem.ai/topup/abc');
});

test('confirming the spend on one item does not approve a different item', async () => {
	// Per-item parameters: n8n evaluates expressions per item, so a Confirm Spend expression that is
	// true for item 0 must not carry over to item 1.
	const base = {
		resource: 'task',
		operation: 'approve',
		taskId: TASK_ID,
		taskName: 'Research',
		price: '$40.00',
	};

	const { output, server } = await execute(
		[
			{ ...base, confirmSpend: true },
			{ ...base, confirmSpend: false },
		],
		{
			items: [{ json: {} }, { json: {} }],
			toolHandler: () => APPROVED,
			continueOnFail: true,
		},
	);

	assert.equal(server.countOf(TENDEM_TOOLS.APPROVE_TASK), 1);
	assert.equal(output[0].json.approved, true);
	assert.match(String(output[1].json.error), /not confirmed/i);
});

test('the wait operation long-polls the server and stops on the first settled snapshot', async () => {
	// The mock answers instantly, so the node's anti-spin backstop kicks in between rounds and sleeps
	// for real. poll_after_seconds=1 keeps that honest but quick; polling.test.js checks the backstop
	// itself against an injected clock.
	const working = { status: 'ACTING', next_action: 'awaiting_tendem_work', poll_after_seconds: 1 };
	const script = [working, working, { status: 'LISTENING', next_action: 'fetch_result' }];

	const { output, server } = await execute(
		{ resource: 'task', operation: 'wait', taskId: TASK_ID, waitForChangeSeconds: 30, maxRounds: 9 },
		{ toolHandler: ({ callIndex }) => script[Math.min(callIndex, script.length - 1)] },
	);

	assert.equal(server.countOf(TENDEM_TOOLS.GET_TASK), 3);
	for (const call of server.toolCalls) {
		assert.equal(call.args.task_id, TASK_ID);
		assert.equal(call.args.wait_for_change_seconds, 30);
	}
	assert.deepEqual(output[0].json.tendemWait, { settled: true, timedOut: false, rounds: 3 });
});

test('an unknown operation is rejected rather than silently doing nothing', async () => {
	await assert.rejects(
		async () => await execute({ resource: 'task', operation: 'somethingElse' }),
		(error) => {
			assert.match(error.message, /task:somethingElse/);
			return true;
		},
	);
});

test('a Tendem tool failure surfaces the server message', async () => {
	await assert.rejects(
		async () =>
			await execute(
				{ resource: 'task', operation: 'get', taskId: TASK_ID },
				{ toolHandler: () => ({ __isError: 'Tool failed (TASK_NOT_FOUND): no such task' }) },
			),
		(error) => {
			assert.match(error.message, /TASK_NOT_FOUND/);
			return true;
		},
	);
});

test('chat send resolves the live offset first by default', async () => {
	const { server } = await execute(
		{ resource: 'chat', operation: 'send', taskId: TASK_ID, text: 'hello' },
		{
			toolHandler: ({ name }) =>
				name === 'read_chat'
					? { messages: [], last_seen_offset: 7 }
					: { response_type: 'sync', last_seen_offset: 8 },
		},
	);

	assert.deepEqual(server.names(), ['read_chat', 'send_message']);
	assert.equal(server.toolCalls[1].args.last_seen_offset, 7);
});

test('a race on send is resolved by re-sending once at the offset the race reported', async () => {
	let sends = 0;
	const { output, server } = await execute(
		{ resource: 'chat', operation: 'send', taskId: TASK_ID, text: 'hello' },
		{
			toolHandler: ({ name }) => {
				if (name === 'read_chat') return { messages: [], last_seen_offset: 3 };
				sends += 1;
				return sends === 1
					? { response_type: 'race', last_seen_offset: 12, messages: [{ text: 'missed' }] }
					: { response_type: 'sync', last_seen_offset: 13 };
			},
		},
	);

	assert.equal(server.countOf('send_message'), 2);
	assert.equal(server.toolCalls.at(-1).args.last_seen_offset, 12);
	assert.equal(output[0].json.response_type, 'sync');
});

test('a second race in a row is surfaced as data, not retried forever', async () => {
	const { output, server } = await execute(
		{ resource: 'chat', operation: 'send', taskId: TASK_ID, text: 'hello' },
		{
			toolHandler: ({ name }) =>
				name === 'read_chat'
					? { messages: [], last_seen_offset: 3 }
					: { response_type: 'race', last_seen_offset: 20, messages: [] },
		},
	);

	assert.equal(server.countOf('send_message'), 2);
	assert.equal(output[0].json.response_type, 'race');
});

test('manual offset threading still works with auto-resolve off', async () => {
	const { server } = await execute(
		{
			resource: 'chat',
			operation: 'send',
			taskId: TASK_ID,
			text: 'hello',
			autoOffset: false,
			lastSeenOffset: 5,
		},
		{ toolHandler: () => ({ response_type: 'sync', last_seen_offset: 6 }) },
	);

	assert.deepEqual(server.names(), ['send_message']);
	assert.equal(server.toolCalls[0].args.last_seen_offset, 5);
});

test('a transient blip during a wait is retried instead of failing the workflow', async () => {
	let calls = 0;
	const { output, server } = await execute(
		{ resource: 'task', operation: 'wait', taskId: TASK_ID, waitForChangeSeconds: 5, maxRounds: 3 },
		{
			toolHandler: () => {
				calls += 1;
				if (calls === 1) {
					return { __isError: 'Tool failed (TEMPORARILY_UNAVAILABLE): blip, retry shortly' };
				}
				return { task_id: TASK_ID, status: 'LISTENING', next_action: 'fetch_result' };
			},
		},
	);

	assert.equal(server.countOf('get_task'), 2);
	assert.equal(output[0].json.tendemWait.settled, true);
});
