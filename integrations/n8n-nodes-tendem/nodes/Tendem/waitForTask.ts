import type { IDataObject } from 'n8n-workflow';

import { TENDEM_TOOLS, type ToolCaller } from './tools';

/**
 * Bounded, server-blocking wait for a Tendem task to change.
 *
 * The waiting happens inside `get_task(task_id, wait_for_change_seconds=N)` — the Tendem server
 * holds the request open until the task actually changes. This module never spins: the round count
 * is hard-capped, and if a round returns faster than the server could plausibly have blocked it
 * backs off before the next one, so a misbehaving endpoint or proxy cannot turn the wait into a
 * busy loop.
 */

/**
 * `next_action` is authoritative over the raw status — the Tendem server says so. This is the one
 * value that means "Tendem is still working, come back later".
 */
export const WAITING_NEXT_ACTION = 'awaiting_tendem_work';

/** `next_action` values that end the wait: something needs the user, or the work is done. */
export const SETTLING_NEXT_ACTIONS = [
	'await_input',
	'await_user_approval',
	'await_user_topup',
	'fetch_result',
	'done',
] as const;

/** Fallback signal when the envelope carries no `next_action`. */
export const ACTIVE_STATUS = 'ACTING';

/** Task statuses. `CLOSED` is terminal but the result is still fetchable. */
export const TASK_STATUSES = ['ACTING', 'LISTENING', 'NEEDS_REPAIR', 'CLOSED', 'DELETED'] as const;

/** `wait_for_change_seconds` is capped at 30 by the Tendem API. */
export const DEFAULT_WAIT_FOR_CHANGE_SECONDS = 30;
export const MIN_WAIT_FOR_CHANGE_SECONDS = 5;
export const MAX_WAIT_FOR_CHANGE_SECONDS = 30;

export const DEFAULT_MAX_ROUNDS = 20;
export const MIN_MAX_ROUNDS = 1;
export const MAX_MAX_ROUNDS = 240;

/** Below this, the server clearly did not block, so back off before polling again. */
export const MIN_SERVER_BLOCK_MS = 1_000;
export const MAX_BACKOFF_MS = 60_000;

export interface WaitDeps {
	caller: ToolCaller;
	sleep(ms: number): Promise<void>;
	now(): number;
}

export interface WaitParams {
	taskId: string;
	waitForChangeSeconds?: number;
	maxRounds?: number;
}

export interface WaitResult {
	/** True when the task no longer needs waiting on. */
	settled: boolean;
	/** True when the round budget ran out while Tendem was still working. */
	timedOut: boolean;
	rounds: number;
	status?: string;
	nextAction?: string;
	task: IDataObject;
}

function clamp(value: number, min: number, max: number): number {
	if (!Number.isFinite(value)) return min;
	return Math.min(Math.max(value, min), max);
}

function readStatus(task: IDataObject): string {
	return typeof task.status === 'string' ? task.status.trim().toUpperCase() : '';
}

function readNextAction(task: IDataObject): string {
	return typeof task.next_action === 'string' ? task.next_action.trim().toLowerCase() : '';
}

/**
 * Whether another round is warranted.
 *
 * Prefers the envelope's `next_action`, falling back to `status` only when it is absent. Anything
 * unrecognised settles the wait rather than continuing — erring toward stopping, never looping.
 */
export function shouldKeepWaiting(task: IDataObject): boolean {
	const nextAction = readNextAction(task);
	if (nextAction !== '') return nextAction === WAITING_NEXT_ACTION;
	return readStatus(task) === ACTIVE_STATUS;
}

/** Back-off for the anti-spin backstop, honouring the server's `poll_after_seconds` hint. */
export function backoffMs(task: IDataObject, waitForChangeSeconds: number): number {
	const hint = task.poll_after_seconds;
	const seconds =
		typeof hint === 'number' && Number.isFinite(hint) && hint > 0 ? hint : waitForChangeSeconds;
	return clamp(Math.round(seconds * 1_000), MIN_SERVER_BLOCK_MS, MAX_BACKOFF_MS);
}

export async function waitForTaskChange(deps: WaitDeps, params: WaitParams): Promise<WaitResult> {
	const waitForChangeSeconds = Math.round(
		clamp(
			params.waitForChangeSeconds ?? DEFAULT_WAIT_FOR_CHANGE_SECONDS,
			MIN_WAIT_FOR_CHANGE_SECONDS,
			MAX_WAIT_FOR_CHANGE_SECONDS,
		),
	);
	const maxRounds = Math.trunc(
		clamp(params.maxRounds ?? DEFAULT_MAX_ROUNDS, MIN_MAX_ROUNDS, MAX_MAX_ROUNDS),
	);

	let task: IDataObject = {};
	let rounds = 0;

	while (rounds < maxRounds) {
		rounds += 1;

		const startedAt = deps.now();
		task = await deps.caller.callTool(TENDEM_TOOLS.GET_TASK, {
			task_id: params.taskId,
			wait_for_change_seconds: waitForChangeSeconds,
		});

		if (!shouldKeepWaiting(task)) {
			return buildResult(true, false, rounds, task);
		}

		if (rounds >= maxRounds) break;

		if (deps.now() - startedAt < MIN_SERVER_BLOCK_MS) {
			await deps.sleep(backoffMs(task, waitForChangeSeconds));
		}
	}

	return buildResult(false, true, rounds, task);
}

function buildResult(
	settled: boolean,
	timedOut: boolean,
	rounds: number,
	task: IDataObject,
): WaitResult {
	const status = readStatus(task);
	const nextAction = readNextAction(task);
	return {
		settled,
		timedOut,
		rounds,
		status: status === '' ? undefined : status,
		nextAction: nextAction === '' ? undefined : nextAction,
		task,
	};
}
