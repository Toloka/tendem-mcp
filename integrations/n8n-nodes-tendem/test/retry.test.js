'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
	MAX_TRANSIENT_RETRIES,
	RetryingToolCaller,
	TRANSIENT_BACKOFF_MAX_MS,
	isTransientError,
	transientBackoffMs,
} = require('../dist/nodes/Tendem/retry.js');
const { McpError } = require('../dist/nodes/Tendem/transport.js');
const { IDEMPOTENT_TOOLS, TENDEM_TOOLS } = require('../dist/nodes/Tendem/tools.js');

function fakeSleep() {
	const sleeps = [];
	return { sleeps, sleep: async (ms) => void sleeps.push(ms) };
}

/** An inner caller that fails `failures` times, then succeeds. */
function flakyCaller(failures, makeError) {
	let attempts = 0;
	return {
		attempts: () => attempts,
		async callTool(name, args) {
			attempts += 1;
			if (attempts <= failures) throw makeError(attempts);
			return { ok: true, name, args };
		},
	};
}

test('a TEMPORARILY_UNAVAILABLE blip during a read is retried until it clears', async () => {
	const inner = flakyCaller(
		2,
		() => new McpError('Tool failed (TEMPORARILY_UNAVAILABLE): busy, try again'),
	);
	const clock = fakeSleep();
	const caller = new RetryingToolCaller(inner, clock);

	const result = await caller.callTool(TENDEM_TOOLS.GET_TASK, { task_id: 't' });

	assert.equal(result.ok, true);
	assert.equal(inner.attempts(), 3);
	assert.deepEqual(clock.sleeps, [2_000, 4_000]);
});

test('transient HTTP statuses are retried; client errors are not', async () => {
	for (const status of [429, 500, 502, 503, 504]) {
		const inner = flakyCaller(1, () => new McpError(`HTTP ${status}`, undefined, undefined, status));
		const caller = new RetryingToolCaller(inner, fakeSleep());
		const result = await caller.callTool(TENDEM_TOOLS.GET_ACCOUNT);
		assert.equal(result.ok, true, `status ${status} should have been retried`);
		assert.equal(inner.attempts(), 2);
	}

	for (const status of [400, 401, 403, 404, 422]) {
		const inner = flakyCaller(1, () => new McpError(`HTTP ${status}`, undefined, undefined, status));
		const caller = new RetryingToolCaller(inner, fakeSleep());
		await assert.rejects(() => caller.callTool(TENDEM_TOOLS.GET_ACCOUNT), McpError);
		assert.equal(inner.attempts(), 1, `status ${status} must not be retried`);
	}
});

test('a network-level failure (not an McpError) is treated as transient', async () => {
	const inner = flakyCaller(1, () => new Error('socket hang up'));
	const caller = new RetryingToolCaller(inner, fakeSleep());
	const result = await caller.callTool(TENDEM_TOOLS.READ_CHAT, { task_id: 't' });
	assert.equal(result.ok, true);
	assert.equal(inner.attempts(), 2);
});

test('a persistent outage gives up after the retry budget and rethrows the real error', async () => {
	const inner = flakyCaller(
		Infinity,
		() => new McpError('Tool failed (TEMPORARILY_UNAVAILABLE): down'),
	);
	const clock = fakeSleep();
	const caller = new RetryingToolCaller(inner, clock);

	await assert.rejects(
		() => caller.callTool(TENDEM_TOOLS.GET_TASK_RESULT, { task_id: 't' }),
		/TEMPORARILY_UNAVAILABLE/,
	);
	assert.equal(inner.attempts(), MAX_TRANSIENT_RETRIES + 1);
	assert.equal(clock.sleeps.length, MAX_TRANSIENT_RETRIES);
});

test('writes are never retried, even on a transient failure', async () => {
	for (const tool of [
		TENDEM_TOOLS.CREATE_TASK,
		TENDEM_TOOLS.SEND_MESSAGE,
		TENDEM_TOOLS.APPROVE_TASK,
	]) {
		const inner = flakyCaller(
			1,
			() => new McpError('Tool failed (TEMPORARILY_UNAVAILABLE): busy'),
		);
		const clock = fakeSleep();
		const caller = new RetryingToolCaller(inner, clock);

		await assert.rejects(() => caller.callTool(tool, {}), /TEMPORARILY_UNAVAILABLE/);
		assert.equal(inner.attempts(), 1, `${tool} must not be retried`);
		assert.deepEqual(clock.sleeps, [], `${tool} must not back off — it must fail immediately`);
		assert.ok(!IDEMPOTENT_TOOLS.includes(tool), `${tool} must not be classified idempotent`);
	}
});

test('backoff doubles from 2s and caps at 60s', () => {
	assert.equal(transientBackoffMs(0), 2_000);
	assert.equal(transientBackoffMs(1), 4_000);
	assert.equal(transientBackoffMs(4), 32_000);
	assert.equal(transientBackoffMs(5), TRANSIENT_BACKOFF_MAX_MS);
	assert.equal(transientBackoffMs(50), TRANSIENT_BACKOFF_MAX_MS);
});

test('a non-Error rejection is not classified as transient', () => {
	assert.equal(isTransientError('boom'), false);
	assert.equal(isTransientError(undefined), false);
});

test('a session-expiry 404 is not swallowed by the retry layer', () => {
	// McpSessionExpiredError carries no httpStatus and its message lacks the transient code, so the
	// session's own reset-and-retry-once logic stays the sole handler for it.
	const { McpSessionExpiredError } = require('../dist/nodes/Tendem/transport.js');
	assert.equal(isTransientError(new McpSessionExpiredError()), false);
});
