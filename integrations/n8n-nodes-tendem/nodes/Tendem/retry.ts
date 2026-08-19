import type { IDataObject } from 'n8n-workflow';

import { McpError } from './transport';
import { IDEMPOTENT_TOOLS, type ToolCaller } from './tools';

/**
 * Transient-failure retry for the read-only Tendem tools.
 *
 * A wait on a human expert runs for minutes to hours, and a wait that long *will* see blips: the
 * server's explicit `TEMPORARILY_UNAVAILABLE`, a gateway 502/503, a dropped connection. Failing the
 * whole workflow on one of those defeats the point of the Wait operation, so idempotent reads are
 * retried with capped exponential backoff. Writes are never retried — retrying `create_task` or
 * `send_message` can duplicate a task or a message, and `approve_task` can duplicate a charge — so
 * a transient failure on a write surfaces immediately and the author decides.
 */

/** The server's explicit "retry me" failure code, quoted inside the tool-failure text. */
export const TRANSIENT_ERROR_CODE = 'TEMPORARILY_UNAVAILABLE';

/** Consecutive transient failures tolerated per call; the counter resets on every success. */
export const MAX_TRANSIENT_RETRIES = 5;

export const TRANSIENT_BACKOFF_BASE_MS = 2_000;
export const TRANSIENT_BACKOFF_MAX_MS = 60_000;

/** HTTP statuses that mean "the server or a proxy hiccupped", not "the request is wrong". */
const TRANSIENT_HTTP_STATUSES: ReadonlySet<number> = new Set([429, 500, 502, 503, 504]);

/**
 * Whether an error is worth retrying. Three shapes qualify: the server's own
 * `TEMPORARILY_UNAVAILABLE` code, a transient HTTP status, and anything that is not an `McpError`
 * at all — the requester (n8n's HTTP helper) throws those only for network-level failures, since
 * HTTP status handling happens inside the transport.
 */
export function isTransientError(error: unknown): boolean {
	if (error instanceof McpError) {
		if (error.httpStatus !== undefined) return TRANSIENT_HTTP_STATUSES.has(error.httpStatus);
		return error.message.includes(TRANSIENT_ERROR_CODE);
	}
	return error instanceof Error;
}

export function transientBackoffMs(attempt: number): number {
	return Math.min(TRANSIENT_BACKOFF_BASE_MS * 2 ** attempt, TRANSIENT_BACKOFF_MAX_MS);
}

export interface RetryDeps {
	sleep(ms: number): Promise<void>;
}

export class RetryingToolCaller implements ToolCaller {
	constructor(
		private readonly inner: ToolCaller,
		private readonly deps: RetryDeps,
		private readonly maxRetries: number = MAX_TRANSIENT_RETRIES,
	) {}

	async callTool(name: string, args: IDataObject = {}): Promise<IDataObject> {
		if (!IDEMPOTENT_TOOLS.includes(name as (typeof IDEMPOTENT_TOOLS)[number])) {
			return await this.inner.callTool(name, args);
		}

		let attempt = 0;
		for (;;) {
			// Settled-outcome rather than try/catch so a non-transient error rethrows with its
			// original stack, undecorated.
			const outcome = await this.inner.callTool(name, args).then(
				(value) => ({ ok: true as const, value }),
				(error: unknown) => ({ ok: false as const, error }),
			);

			if (outcome.ok) return outcome.value;
			if (!isTransientError(outcome.error) || attempt >= this.maxRetries) throw outcome.error;

			await this.deps.sleep(transientBackoffMs(attempt));
			attempt += 1;
		}
	}
}
