'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
	DEFAULT_MAX_ROUNDS,
	MAX_BACKOFF_MS,
	MAX_MAX_ROUNDS,
	MAX_WAIT_FOR_CHANGE_SECONDS,
	backoffMs,
	shouldKeepWaiting,
	waitForTaskChange,
} = require('../dist/nodes/Tendem/waitForTask.js');
const { TENDEM_TOOLS } = require('../dist/nodes/Tendem/tools.js');
const { makeClock } = require('./harness.js');

const WORKING = { status: 'ACTING', next_action: 'awaiting_tendem_work', poll_after_seconds: 30 };

/** A caller that replays a script of task snapshots and advances the simulated clock. */
function scriptedCaller(script, clock) {
	const calls = [];
	return {
		calls,
		async callTool(name, args) {
			calls.push({ name, args });
			clock.advanceForRequest();
			const index = Math.min(calls.length - 1, script.length - 1);
			return script[index];
		},
	};
}

function run(script, params, clockOptions) {
	const clock = makeClock(clockOptions);
	const caller = scriptedCaller(script, clock);
	return waitForTaskChange({ caller, sleep: clock.sleep, now: clock.now }, params).then(
		(result) => ({ result, caller, clock }),
	);
}

test('polling terminates when Tendem never stops working', async () => {
	const { result, caller } = await run([WORKING], { taskId: 't1', maxRounds: 5 });

	assert.equal(result.settled, false);
	assert.equal(result.timedOut, true);
	assert.equal(result.rounds, 5);
	// Bounded: exactly the round budget, not one more.
	assert.equal(caller.calls.length, 5);
	assert.ok(caller.calls.every((c) => c.name === TENDEM_TOOLS.GET_TASK));
});

test('polling never issues approve_task', async () => {
	const { caller } = await run([WORKING], { taskId: 't1', maxRounds: 8 });
	assert.equal(caller.calls.filter((c) => c.name === TENDEM_TOOLS.APPROVE_TASK).length, 0);
	assert.deepEqual(new Set(caller.calls.map((c) => c.name)), new Set([TENDEM_TOOLS.GET_TASK]));
});

test('the round budget is respected for a large range of caps', async () => {
	for (const maxRounds of [1, 2, 3, 7, 20, 50]) {
		const { result, caller } = await run([WORKING], { taskId: 't1', maxRounds });
		assert.equal(caller.calls.length, maxRounds);
		assert.equal(result.rounds, maxRounds);
		assert.equal(result.timedOut, true);
	}
});

test('an out-of-range or missing round budget still terminates', async () => {
	for (const maxRounds of [0, -5, undefined, Number.NaN, 10_000, Infinity]) {
		const { result, caller } = await run([WORKING], { taskId: 't1', maxRounds });
		assert.ok(caller.calls.length >= 1);
		assert.ok(
			caller.calls.length <= MAX_MAX_ROUNDS,
			`maxRounds=${String(maxRounds)} produced ${caller.calls.length} calls`,
		);
		assert.equal(result.timedOut, true);
	}
});

test('the default round budget is bounded', async () => {
	const { caller } = await run([WORKING], { taskId: 't1' });
	assert.equal(caller.calls.length, DEFAULT_MAX_ROUNDS);
});

test('waiting stops as soon as the task needs the user or finishes', async () => {
	const settling = [
		{ next_action: 'await_input', status: 'LISTENING' },
		{ next_action: 'await_user_approval', status: 'LISTENING' },
		{ next_action: 'await_user_topup', status: 'LISTENING' },
		{ next_action: 'fetch_result', status: 'LISTENING' },
		{ next_action: 'done', status: 'CLOSED' },
	];

	for (const snapshot of settling) {
		const { result, caller } = await run([WORKING, WORKING, snapshot], {
			taskId: 't1',
			maxRounds: 20,
		});
		assert.equal(result.settled, true, `${snapshot.next_action} should settle`);
		assert.equal(result.timedOut, false);
		assert.equal(result.rounds, 3);
		assert.equal(caller.calls.length, 3, 'must stop calling once settled');
		assert.equal(result.nextAction, snapshot.next_action);
	}
});

test('waiting falls back to status when the envelope has no next_action', async () => {
	for (const status of ['LISTENING', 'NEEDS_REPAIR', 'CLOSED', 'DELETED']) {
		const { result } = await run([{ status }], { taskId: 't1', maxRounds: 9 });
		assert.equal(result.settled, true, `${status} should settle`);
		assert.equal(result.rounds, 1);
		assert.equal(result.status, status);
	}

	const stillWorking = await run([{ status: 'ACTING' }], { taskId: 't1', maxRounds: 4 });
	assert.equal(stillWorking.result.settled, false);
	assert.equal(stillWorking.result.rounds, 4);
});

test('an unrecognised or empty snapshot settles rather than looping', async () => {
	for (const snapshot of [{}, { status: 'SOMETHING_NEW' }, { next_action: 'who_knows' }]) {
		const { result, caller } = await run([snapshot], { taskId: 't1', maxRounds: 30 });
		assert.equal(result.settled, true);
		assert.equal(caller.calls.length, 1);
	}
});

test('next_action wins over a contradictory status', async () => {
	// Envelope says work is done even though status still reads ACTING.
	const { result, caller } = await run([{ status: 'ACTING', next_action: 'fetch_result' }], {
		taskId: 't1',
		maxRounds: 10,
	});
	assert.equal(result.settled, true);
	assert.equal(caller.calls.length, 1);
});

test('the wait is delegated to the server via wait_for_change_seconds', async () => {
	const { caller } = await run([WORKING], { taskId: 't-42', waitForChangeSeconds: 30, maxRounds: 3 });

	for (const call of caller.calls) {
		assert.equal(call.args.task_id, 't-42');
		assert.equal(call.args.wait_for_change_seconds, 30);
	}
});

test('wait_for_change_seconds is clamped to the API maximum of 30', async () => {
	for (const [requested, expected] of [
		[0, 5],
		[1, 5],
		[5, 5],
		[17, 17],
		[30, 30],
		[45, 30],
		[100_000, 30],
		[Number.NaN, 5],
	]) {
		const { caller } = await run([WORKING], {
			taskId: 't1',
			waitForChangeSeconds: requested,
			maxRounds: 1,
		});
		assert.equal(
			caller.calls[0].args.wait_for_change_seconds,
			expected,
			`requested ${String(requested)}`,
		);
		assert.ok(caller.calls[0].args.wait_for_change_seconds <= MAX_WAIT_FOR_CHANGE_SECONDS);
	}
});

test('no client-side sleeping happens when the server actually blocks', async () => {
	// Each request "takes" 30s, i.e. the server held it open.
	const { clock, caller } = await run([WORKING], { taskId: 't1', maxRounds: 5 });
	assert.equal(caller.calls.length, 5);
	assert.deepEqual(clock.sleeps, [], 'should not sleep when the server blocked');
});

test('instant server responses trigger back-off instead of a busy loop', async () => {
	// Server ignores wait_for_change_seconds and answers immediately.
	const { clock, caller } = await run(
		[WORKING],
		{ taskId: 't1', maxRounds: 6 },
		{ requestDurationMs: 0 },
	);

	assert.equal(caller.calls.length, 6);
	// One back-off between each pair of rounds, none after the last.
	assert.equal(clock.sleeps.length, 5);
	for (const ms of clock.sleeps) {
		assert.ok(ms >= 1_000, `back-off ${ms}ms is too short to prevent a busy loop`);
		assert.ok(ms <= MAX_BACKOFF_MS);
	}
	// 6 instant rounds still consumed real time rather than spinning.
	assert.ok(clock.now() >= 5_000);
});

test('back-off honours the server poll_after_seconds hint and stays bounded', () => {
	assert.equal(backoffMs({ poll_after_seconds: 5 }, 30), 5_000);
	assert.equal(backoffMs({ poll_after_seconds: 300 }, 30), MAX_BACKOFF_MS);
	assert.equal(backoffMs({ poll_after_seconds: 0 }, 30), 30_000);
	assert.equal(backoffMs({ poll_after_seconds: -1 }, 30), 30_000);
	assert.equal(backoffMs({ poll_after_seconds: 'soon' }, 30), 30_000);
	assert.equal(backoffMs({}, 30), 30_000);
	// Never below the floor, however small the hint.
	assert.equal(backoffMs({ poll_after_seconds: 0.001 }, 30), 1_000);
});

test('shouldKeepWaiting only continues while Tendem is working', () => {
	assert.equal(shouldKeepWaiting({ next_action: 'awaiting_tendem_work' }), true);
	assert.equal(shouldKeepWaiting({ next_action: 'AWAITING_TENDEM_WORK' }), true);
	assert.equal(shouldKeepWaiting({ status: 'ACTING' }), true);
	assert.equal(shouldKeepWaiting({ status: 'acting' }), true);

	assert.equal(shouldKeepWaiting({ next_action: 'done' }), false);
	assert.equal(shouldKeepWaiting({ next_action: 'fetch_result', status: 'ACTING' }), false);
	assert.equal(shouldKeepWaiting({ status: 'CLOSED' }), false);
	assert.equal(shouldKeepWaiting({}), false);
});

test('the last snapshot is returned even when the budget runs out', async () => {
	const last = { status: 'ACTING', next_action: 'awaiting_tendem_work', price: '$40.00' };
	const { result } = await run([WORKING, last], { taskId: 't1', maxRounds: 3 });
	assert.equal(result.timedOut, true);
	assert.equal(result.task.price, '$40.00');
});
