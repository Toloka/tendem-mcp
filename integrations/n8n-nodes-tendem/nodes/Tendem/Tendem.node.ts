import {
	NodeApiError,
	NodeConnectionTypes,
	NodeOperationError,
	sleep,
	type IDataObject,
	type IExecuteFunctions,
	type INodeExecutionData,
	type INodeType,
	type INodeTypeDescription,
	type JsonObject,
} from 'n8n-workflow';

import {
	accountOperations,
	chatFields,
	chatOperations,
	fileFields,
	fileOperations,
	resourceField,
	taskFields,
	taskOperations,
} from './descriptions';
import {
	McpSession,
	TENDEM_DEFAULT_ENDPOINT,
	type McpHttpRequestOptions,
	type McpHttpResponse,
} from './transport';
import { RetryingToolCaller } from './retry';
import { guardFor, operationKey, TENDEM_TOOLS, type ToolCaller } from './tools';
import { waitForTaskChange } from './waitForTask';

/**
 * `usableAsTool` is omitted on purpose, and the omission is the guardrail.
 *
 * Tool exposure is all-or-nothing per node: n8n types the flag `true | { replacements }` — there is
 * no form of it that withholds a single operation. Enabling it would therefore hand an AI agent the
 * "Approve (Spends Money)" operation, the one operation that charges the Tendem account. Spend is a
 * workflow author's decision, not a model's, so this node stays off the tool surface. An agent that
 * needs the read-only parts (create, get, contract, result) can reach the Tendem MCP server direct.
 */
// eslint-disable-next-line @n8n/community-nodes/node-usable-as-tool -- deliberate: enabling it would expose approve_task, which spends real money, to an LLM
export class Tendem implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'Tendem',
		name: 'tendem',
		icon: 'file:tendem.svg',
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"] + ": " + $parameter["resource"]}}',
		description: 'Delegate work to vetted human experts through Tendem',
		defaults: { name: 'Tendem' },
		// No `usableAsTool` here — see the note on the class.
		inputs: [NodeConnectionTypes.Main],
		outputs: [NodeConnectionTypes.Main],
		credentials: [{ name: 'tendemApi', required: true }],
		properties: [
			resourceField,
			taskOperations,
			chatOperations,
			accountOperations,
			fileOperations,
			...taskFields,
			...chatFields,
			...fileFields,
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const returnData: INodeExecutionData[] = [];

		const credentials = await this.getCredentials('tendemApi');
		const endpoint =
			typeof credentials.endpoint === 'string' && credentials.endpoint.trim() !== ''
				? credentials.endpoint.trim()
				: TENDEM_DEFAULT_ENDPOINT;

		const requester = async (options: McpHttpRequestOptions): Promise<McpHttpResponse> =>
			(await this.helpers.httpRequestWithAuthentication.call(
				this,
				'tendemApi',
				options,
			)) as McpHttpResponse;

		const session = new McpSession(requester, { endpoint });
		// Transient failures (TEMPORARILY_UNAVAILABLE, 5xx, dropped connections) are retried with
		// backoff — but only for idempotent reads; writes surface their failures immediately.
		const retrying = new RetryingToolCaller(session, { sleep });

		for (let i = 0; i < items.length; i += 1) {
			try {
				const resource = this.getNodeParameter('resource', i) as string;
				const operation = this.getNodeParameter('operation', i) as string;
				const key = operationKey(resource, operation);

				// Each item runs against a capability-scoped caller. `approve_task` is unreachable
				// from every operation except `task:approve`.
				const guarded: ToolCaller = guardFor(retrying, key);

				const payload = await runOperation.call(this, guarded, key, i);

				returnData.push(
					...this.helpers.constructExecutionMetaData(this.helpers.returnJsonArray(payload), {
						itemData: { item: i },
					}),
				);
			} catch (error) {
				if (this.continueOnFail()) {
					returnData.push({
						json: { error: error instanceof Error ? error.message : String(error) },
						pairedItem: { item: i },
					});
					continue;
				}
				throw asNodeError.call(this, error, i);
			}
		}

		return [returnData];
	}
}

/**
 * Normalises anything thrown inside the item loop into an n8n node error, so the editor shows the
 * Tendem message and the failing item rather than a bare stack trace. Errors this node raised on
 * purpose — the approval guardrails and the capability guard — are already node errors or carry a
 * message worth showing verbatim, so they pass through with their text intact.
 */
function asNodeError(this: IExecuteFunctions, error: unknown, itemIndex: number): Error {
	if (error instanceof NodeOperationError || error instanceof NodeApiError) return error;
	return new NodeApiError(this.getNode(), error as JsonObject, {
		itemIndex,
		message: error instanceof Error ? error.message : String(error),
	});
}

async function runOperation(
	this: IExecuteFunctions,
	guarded: ToolCaller,
	key: string,
	i: number,
): Promise<IDataObject> {
	switch (key) {
		case 'task:create': {
			const args: IDataObject = {
				name: this.getNodeParameter('taskName', i) as string,
				description: this.getNodeParameter('description', i) as string,
			};
			const conversationId = this.getNodeParameter('conversationId', i, '') as string;
			if (conversationId !== '') args.conversation_id = conversationId;
			return await guarded.callTool(TENDEM_TOOLS.CREATE_TASK, args);
		}

		case 'task:get':
			return await guarded.callTool(TENDEM_TOOLS.GET_TASK, {
				task_id: this.getNodeParameter('taskId', i) as string,
			});

		case 'task:getContract':
			return await guarded.callTool(TENDEM_TOOLS.GET_CONTRACT, {
				task_id: this.getNodeParameter('taskId', i) as string,
			});

		case 'task:approve':
			return await approveTask.call(this, guarded, i);

		case 'task:cancel':
			return await guarded.callTool(TENDEM_TOOLS.CANCEL_TASK, {
				task_id: this.getNodeParameter('taskId', i) as string,
				name: this.getNodeParameter('taskName', i) as string,
			});

		case 'task:getResult':
			return await guarded.callTool(TENDEM_TOOLS.GET_TASK_RESULT, {
				task_id: this.getNodeParameter('taskId', i) as string,
			});

		case 'task:list':
			return await guarded.callTool(TENDEM_TOOLS.LIST_TASKS, {
				limit: this.getNodeParameter('limit', i, 50) as number,
				offset: this.getNodeParameter('offset', i, 0) as number,
			});

		case 'task:wait': {
			const outcome = await waitForTaskChange(
				{ caller: guarded, sleep, now: () => Date.now() },
				{
					taskId: this.getNodeParameter('taskId', i) as string,
					waitForChangeSeconds: this.getNodeParameter('waitForChangeSeconds', i, 30) as number,
					maxRounds: this.getNodeParameter('maxRounds', i, 20) as number,
				},
			);
			return {
				...outcome.task,
				tendemWait: {
					settled: outcome.settled,
					timedOut: outcome.timedOut,
					rounds: outcome.rounds,
				},
			};
		}

		case 'chat:read':
			return await guarded.callTool(TENDEM_TOOLS.READ_CHAT, {
				task_id: this.getNodeParameter('taskId', i) as string,
				from_offset: this.getNodeParameter('fromOffset', i, 0) as number,
			});

		case 'chat:send':
			return await sendChatMessage.call(this, guarded, i);

		case 'account:get':
			return await guarded.callTool(TENDEM_TOOLS.GET_ACCOUNT, {});

		case 'file:getUploadUrl':
			return await guarded.callTool(TENDEM_TOOLS.GET_FILE_UPLOAD_URL, {
				task_id: this.getNodeParameter('taskId', i) as string,
			});

		default:
			throw new NodeOperationError(this.getNode(), `Unsupported Tendem operation "${key}"`, {
				itemIndex: i,
			});
	}
}

/**
 * Sends a chat message without falling into the silent-drop race.
 *
 * `send_message` refuses delivery when Tendem posted new content after the offset the caller last
 * saw (`response_type: "race"`) — correct protocol behaviour, but a workflow that hardcodes
 * `last_seen_offset: 0` would silently lose every reply after the first exchange. By default the
 * node therefore reads the chat first and sends at the live offset, and if Tendem still races it
 * (something arrived in the window between read and send), it re-resolves and re-sends once. A
 * second race in a row is surfaced as data — `response_type: "race"` with the missed messages —
 * because at that point the conversation has moved and the author should see what changed before
 * insisting. Turning "Resolve Offset Automatically" off restores fully manual offset threading.
 */
async function sendChatMessage(
	this: IExecuteFunctions,
	guarded: ToolCaller,
	i: number,
): Promise<IDataObject> {
	const taskId = this.getNodeParameter('taskId', i) as string;
	const text = this.getNodeParameter('text', i) as string;
	const autoOffset = this.getNodeParameter('autoOffset', i, true) as boolean;

	const latestOffset = async (): Promise<number> => {
		const chat = await guarded.callTool(TENDEM_TOOLS.READ_CHAT, {
			task_id: taskId,
			from_offset: 0,
		});
		return typeof chat.last_seen_offset === 'number' ? chat.last_seen_offset : 0;
	};

	let offset = autoOffset
		? await latestOffset()
		: (this.getNodeParameter('lastSeenOffset', i, 0) as number);

	let response = await guarded.callTool(TENDEM_TOOLS.SEND_MESSAGE, {
		task_id: taskId,
		text,
		last_seen_offset: offset,
	});

	if (autoOffset && response.response_type === 'race') {
		offset =
			typeof response.last_seen_offset === 'number' ? response.last_seen_offset : await latestOffset();
		response = await guarded.callTool(TENDEM_TOOLS.SEND_MESSAGE, {
			task_id: taskId,
			text,
			last_seen_offset: offset,
		});
	}

	return response;
}

/**
 * The only code path that reaches `approve_task`.
 *
 * Three gates stand in front of the spend: the workflow author must have added this operation
 * deliberately, `confirmSpend` must be true, and the price has to be supplied — which means the
 * amount being committed was read before it could be committed. Insufficient balance comes back as
 * data rather than an exception, so a workflow can route the user to the task-bound top-up URL.
 * The node never retries and never tops up.
 */
async function approveTask(
	this: IExecuteFunctions,
	guarded: ToolCaller,
	i: number,
): Promise<IDataObject> {
	const taskId = this.getNodeParameter('taskId', i) as string;
	const taskName = this.getNodeParameter('taskName', i) as string;
	const price = this.getNodeParameter('price', i) as string;
	const confirmed = this.getNodeParameter('confirmSpend', i, false) as boolean;

	if (confirmed !== true) {
		throw new NodeOperationError(
			this.getNode(),
			'Refusing to approve a Tendem task: the spend was not confirmed',
			{
				itemIndex: i,
				description:
					'Approving charges the Tendem account. Read the scope and price with the "Get Contract" operation, surface it to a human, then enable "Confirm Spend" on this node.',
			},
		);
	}

	if (typeof price !== 'string' || price.trim() === '') {
		throw new NodeOperationError(
			this.getNode(),
			'Refusing to approve a Tendem task: no price was supplied',
			{
				itemIndex: i,
				description:
					'Pass the quoted price through from the Get Task or Get Contract operation, so the approval names the amount being committed.',
			},
		);
	}

	const response = await guarded.callTool(TENDEM_TOOLS.APPROVE_TASK, {
		task_id: taskId,
		name: taskName,
		price,
	});

	const approvedRaw = response.approved;
	const approved = typeof approvedRaw === 'boolean' ? approvedRaw : undefined;

	return {
		...response,
		approved: approved === undefined ? null : approved,
		spendBlocked: approved === false,
		topupUrl: typeof response.topup_url === 'string' ? response.topup_url : null,
	};
}
