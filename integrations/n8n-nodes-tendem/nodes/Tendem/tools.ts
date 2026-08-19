import type { IDataObject } from 'n8n-workflow';

/**
 * The 11 tools exposed by the Tendem MCP server.
 */
export const TENDEM_TOOLS = {
	CREATE_TASK: 'create_task',
	GET_TASK: 'get_task',
	GET_CONTRACT: 'get_contract',
	APPROVE_TASK: 'approve_task',
	CANCEL_TASK: 'cancel_task',
	GET_TASK_RESULT: 'get_task_result',
	LIST_TASKS: 'list_tasks',
	READ_CHAT: 'read_chat',
	SEND_MESSAGE: 'send_message',
	GET_ACCOUNT: 'get_account',
	GET_FILE_UPLOAD_URL: 'get_file_upload_url',
} as const;

export type TendemToolName = (typeof TENDEM_TOOLS)[keyof typeof TENDEM_TOOLS];

/**
 * Tools that commit real money. Approving a Tendem task charges the account, so this must never
 * become a side effect of creating, polling, or reading a task.
 */
export const SPEND_COMMITTING_TOOLS: readonly TendemToolName[] = [TENDEM_TOOLS.APPROVE_TASK];

/** The only operation permitted to reach a spend-committing tool. */
export const SPEND_OPERATION_KEY = 'task:approve';

/**
 * Tools that are safe to repeat: pure reads, plus `cancel_task` and `get_file_upload_url`, which
 * only mint URLs and mutate nothing. Everything else — `create_task`, `send_message`,
 * `approve_task` — can duplicate a task, a message, or a charge when retried, so the transient
 * retry in {@link ../retry} refuses to touch them.
 */
export const IDEMPOTENT_TOOLS: readonly TendemToolName[] = [
	TENDEM_TOOLS.GET_TASK,
	TENDEM_TOOLS.GET_CONTRACT,
	TENDEM_TOOLS.CANCEL_TASK,
	TENDEM_TOOLS.GET_TASK_RESULT,
	TENDEM_TOOLS.LIST_TASKS,
	TENDEM_TOOLS.READ_CHAT,
	TENDEM_TOOLS.GET_ACCOUNT,
	TENDEM_TOOLS.GET_FILE_UPLOAD_URL,
];

/**
 * Per-operation capability allowlist.
 *
 * This is the structural half of the "never approve spend implicitly" guarantee: each operation
 * runs against a {@link GuardedToolCaller} built from its own row here, so a bug or a future edit
 * in, say, the polling branch cannot issue `approve_task` — the guard rejects it before any HTTP
 * request is made. Exactly one row contains `approve_task`, and a test enforces that.
 */
export const OPERATION_TOOL_ALLOWLIST: Readonly<Record<string, readonly TendemToolName[]>> = {
	'task:create': [TENDEM_TOOLS.CREATE_TASK],
	'task:get': [TENDEM_TOOLS.GET_TASK],
	'task:wait': [TENDEM_TOOLS.GET_TASK],
	'task:getContract': [TENDEM_TOOLS.GET_CONTRACT],
	'task:approve': [TENDEM_TOOLS.APPROVE_TASK],
	'task:cancel': [TENDEM_TOOLS.CANCEL_TASK],
	'task:getResult': [TENDEM_TOOLS.GET_TASK_RESULT],
	'task:list': [TENDEM_TOOLS.LIST_TASKS],
	'chat:read': [TENDEM_TOOLS.READ_CHAT],
	// Send may also read: resolving the live offset before sending is how it avoids the silent
	// "race" drop when Tendem posted new content first.
	'chat:send': [TENDEM_TOOLS.SEND_MESSAGE, TENDEM_TOOLS.READ_CHAT],
	'account:get': [TENDEM_TOOLS.GET_ACCOUNT],
	'file:getUploadUrl': [TENDEM_TOOLS.GET_FILE_UPLOAD_URL],
};

export interface ToolCaller {
	callTool(name: string, args?: IDataObject): Promise<IDataObject>;
}

export class ToolNotPermittedError extends Error {
	constructor(message: string) {
		super(message);
		this.name = 'ToolNotPermittedError';
	}
}

export class GuardedToolCaller implements ToolCaller {
	constructor(
		private readonly inner: ToolCaller,
		private readonly operationKey: string,
		private readonly allowed: readonly string[],
	) {}

	async callTool(name: string, args: IDataObject = {}): Promise<IDataObject> {
		if (!this.allowed.includes(name)) {
			throw new ToolNotPermittedError(
				`The Tendem "${this.operationKey}" operation is not permitted to call "${name}". ` +
					`Permitted: ${this.allowed.join(', ')}.` +
					(SPEND_COMMITTING_TOOLS.includes(name as TendemToolName)
						? ` "${name}" spends real money and is only reachable from the dedicated "Approve Task" operation.`
						: ''),
			);
		}
		return await this.inner.callTool(name, args);
	}
}

export function operationKey(resource: string, operation: string): string {
	return `${resource}:${operation}`;
}

export function guardFor(inner: ToolCaller, key: string): GuardedToolCaller {
	const allowed = OPERATION_TOOL_ALLOWLIST[key];
	if (allowed === undefined) {
		throw new ToolNotPermittedError(`Unknown Tendem operation "${key}"`);
	}
	return new GuardedToolCaller(inner, key, allowed);
}
