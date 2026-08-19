'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
	GuardedToolCaller,
	OPERATION_TOOL_ALLOWLIST,
	SPEND_COMMITTING_TOOLS,
	SPEND_OPERATION_KEY,
	TENDEM_TOOLS,
	ToolNotPermittedError,
	guardFor,
} = require('../dist/nodes/Tendem/tools.js');

const ALL_TOOLS = Object.values(TENDEM_TOOLS);

function spyCaller() {
	const calls = [];
	return {
		calls,
		async callTool(name, args) {
			calls.push({ name, args });
			return { ok: true };
		},
	};
}

test('the Tendem tool surface is exactly the 11 documented tools', () => {
	assert.equal(ALL_TOOLS.length, 11);
	assert.deepEqual(
		[...ALL_TOOLS].sort(),
		[
			'approve_task',
			'cancel_task',
			'create_task',
			'get_account',
			'get_contract',
			'get_file_upload_url',
			'get_task',
			'get_task_result',
			'list_tasks',
			'read_chat',
			'send_message',
		].sort(),
	);
});

test('approve_task is the only tool classified as spend-committing', () => {
	assert.deepEqual([...SPEND_COMMITTING_TOOLS], [TENDEM_TOOLS.APPROVE_TASK]);
});

test('exactly one operation is allowed to reach a spend-committing tool', () => {
	const operationsThatCanSpend = Object.entries(OPERATION_TOOL_ALLOWLIST)
		.filter(([, tools]) => tools.some((tool) => SPEND_COMMITTING_TOOLS.includes(tool)))
		.map(([key]) => key);

	assert.deepEqual(operationsThatCanSpend, [SPEND_OPERATION_KEY]);
});

test('every operation allowlist is non-empty and names only real Tendem tools', () => {
	for (const [key, tools] of Object.entries(OPERATION_TOOL_ALLOWLIST)) {
		assert.ok(tools.length > 0, `${key} has an empty allowlist`);
		for (const tool of tools) {
			assert.ok(ALL_TOOLS.includes(tool), `${key} references unknown tool ${tool}`);
		}
	}
});

test('all 11 tools stay reachable through some operation', () => {
	const reachable = new Set(Object.values(OPERATION_TOOL_ALLOWLIST).flat());
	for (const tool of ALL_TOOLS) {
		assert.ok(reachable.has(tool), `${tool} is not reachable from any operation`);
	}
});

test('the guard blocks approve_task from every operation except the approval one', async () => {
	for (const key of Object.keys(OPERATION_TOOL_ALLOWLIST)) {
		if (key === SPEND_OPERATION_KEY) continue;

		const inner = spyCaller();
		const guarded = guardFor(inner, key);

		await assert.rejects(
			async () => await guarded.callTool(TENDEM_TOOLS.APPROVE_TASK, { task_id: 't' }),
			ToolNotPermittedError,
			`${key} was able to call approve_task`,
		);

		// The refusal happens before any I/O.
		assert.equal(inner.calls.length, 0, `${key} reached the transport with approve_task`);
	}
});

test('the guard lets the approval operation through to approve_task', async () => {
	const inner = spyCaller();
	const guarded = guardFor(inner, SPEND_OPERATION_KEY);

	await guarded.callTool(TENDEM_TOOLS.APPROVE_TASK, { task_id: 't', name: 'n', price: '$1' });

	assert.deepEqual(inner.calls.map((c) => c.name), [TENDEM_TOOLS.APPROVE_TASK]);
});

test('the approval operation cannot call anything other than approve_task', async () => {
	const inner = spyCaller();
	const guarded = guardFor(inner, SPEND_OPERATION_KEY);

	await assert.rejects(
		async () => await guarded.callTool(TENDEM_TOOLS.CREATE_TASK, {}),
		ToolNotPermittedError,
	);
	assert.equal(inner.calls.length, 0);
});

test('each operation can only call the tools on its own allowlist', async () => {
	for (const [key, allowed] of Object.entries(OPERATION_TOOL_ALLOWLIST)) {
		for (const tool of ALL_TOOLS) {
			const inner = spyCaller();
			const guarded = guardFor(inner, key);

			if (allowed.includes(tool)) {
				await guarded.callTool(tool, {});
				assert.equal(inner.calls.length, 1, `${key} should permit ${tool}`);
			} else {
				await assert.rejects(
					async () => await guarded.callTool(tool, {}),
					ToolNotPermittedError,
					`${key} should refuse ${tool}`,
				);
				assert.equal(inner.calls.length, 0, `${key} leaked ${tool} to the transport`);
			}
		}
	}
});

test('the refusal message explains the money risk for spend-committing tools', async () => {
	const guarded = guardFor(spyCaller(), 'task:wait');
	await assert.rejects(
		async () => await guarded.callTool(TENDEM_TOOLS.APPROVE_TASK, {}),
		(error) => {
			assert.match(error.message, /spends real money/);
			assert.match(error.message, /task:wait/);
			return true;
		},
	);
});

test('guardFor refuses unknown operation keys instead of defaulting open', () => {
	assert.throws(() => guardFor(spyCaller(), 'task:definitelyNotAnOperation'), ToolNotPermittedError);
	assert.throws(() => guardFor(spyCaller(), ''), ToolNotPermittedError);
});

test('a guard constructed with an empty allowlist permits nothing', async () => {
	const inner = spyCaller();
	const guarded = new GuardedToolCaller(inner, 'task:wait', []);
	for (const tool of ALL_TOOLS) {
		await assert.rejects(async () => await guarded.callTool(tool, {}), ToolNotPermittedError);
	}
	assert.equal(inner.calls.length, 0);
});

test('the MCP client version constant matches package.json', () => {
	const { MCP_CLIENT_VERSION, MCP_CLIENT_NAME } = require('../dist/nodes/Tendem/transport.js');
	const pkg = require('../package.json');
	assert.equal(MCP_CLIENT_VERSION, pkg.version);
	assert.equal(MCP_CLIENT_NAME, pkg.name);
});
