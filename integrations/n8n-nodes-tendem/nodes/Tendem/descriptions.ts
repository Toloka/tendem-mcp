import type { INodeProperties } from 'n8n-workflow';

import {
	DEFAULT_MAX_ROUNDS,
	DEFAULT_WAIT_FOR_CHANGE_SECONDS,
	MAX_MAX_ROUNDS,
	MAX_WAIT_FOR_CHANGE_SECONDS,
	MIN_MAX_ROUNDS,
	MIN_WAIT_FOR_CHANGE_SECONDS,
} from './waitForTask';

const showFor = (resource: string, operations: string[]): INodeProperties['displayOptions'] => ({
	show: { resource: [resource], operation: operations },
});

export const resourceField: INodeProperties = {
	displayName: 'Resource',
	name: 'resource',
	type: 'options',
	noDataExpression: true,
	default: 'task',
	options: [
		{ name: 'Task', value: 'task' },
		{ name: 'Chat', value: 'chat' },
		{ name: 'Account', value: 'account' },
		{ name: 'File', value: 'file' },
	],
};

export const taskOperations: INodeProperties = {
	displayName: 'Operation',
	name: 'operation',
	type: 'options',
	noDataExpression: true,
	default: 'create',
	displayOptions: { show: { resource: ['task'] } },
	// n8n's community lint requires these to be alphabetized by `name`, so the running order is
	// Create → Get Contract → Approve, not the order they appear in here. `default` below, not
	// position, decides what the node starts on.
	options: [
		{
			name: 'Approve (Spends Money)',
			value: 'approve',
			description:
				'Approve the scope and quote so a human expert starts work. This charges the Tendem account and requires explicit confirmation.',
			action: 'Approve a task and commit the spend',
		},
		{
			name: 'Create',
			value: 'create',
			description: 'Submit a brief. Tendem scopes and prices it — nothing is charged yet.',
			action: 'Create a task',
		},
		{
			name: 'Get',
			value: 'get',
			description: 'Read a task snapshot once, including price and approval readiness',
			action: 'Get a task',
		},
		{
			name: 'Get Cancel URL',
			value: 'cancel',
			description:
				'Get the Tendem UI URL where the user can cancel. This does not cancel anything by itself.',
			action: 'Get the cancel URL for a task',
		},
		{
			name: 'Get Contract',
			value: 'getContract',
			description: 'Read the full scope and price the user would be approving',
			action: 'Get the contract for a task',
		},
		{
			name: 'Get Result',
			value: 'getResult',
			description: 'Fetch the result markdown plus downloadable file URLs',
			action: 'Get the result of a task',
		},
		{
			name: 'List',
			value: 'list',
			description: 'List tasks on the account, useful for recovering task IDs',
			action: 'List tasks',
		},
		{
			name: 'Wait for Change',
			value: 'wait',
			description:
				'Block until the task needs attention or finishes, using a bounded number of server-side waits',
			action: 'Wait for a task to change',
		},
	],
};

export const chatOperations: INodeProperties = {
	displayName: 'Operation',
	name: 'operation',
	type: 'options',
	noDataExpression: true,
	default: 'read',
	displayOptions: { show: { resource: ['chat'] } },
	options: [
		{
			name: 'Read',
			value: 'read',
			description: 'Read the scoping conversation from a cursor',
			action: 'Read the chat for a task',
		},
		{
			name: 'Send',
			value: 'send',
			description: 'Answer a Tendem scoping question, add context, or tell Tendem to start',
			action: 'Send a message to a task',
		},
	],
};

export const accountOperations: INodeProperties = {
	displayName: 'Operation',
	name: 'operation',
	type: 'options',
	noDataExpression: true,
	default: 'get',
	displayOptions: { show: { resource: ['account'] } },
	options: [
		{
			name: 'Get',
			value: 'get',
			description: 'Read the account balance and generic top-up URL',
			action: 'Get the account',
		},
	],
};

export const fileOperations: INodeProperties = {
	displayName: 'Operation',
	name: 'operation',
	type: 'options',
	noDataExpression: true,
	default: 'getUploadUrl',
	displayOptions: { show: { resource: ['file'] } },
	options: [
		{
			name: 'Get Upload URL',
			value: 'getUploadUrl',
			description:
				'Get a short-lived folder URL for attaching input files to a task, then name them in a chat message',
			action: 'Get a file upload URL',
		},
	],
};

export const taskFields: INodeProperties[] = [
	{
		displayName: 'Name',
		name: 'taskName',
		type: 'string',
		default: '',
		required: true,
		displayOptions: showFor('task', ['create']),
		description: 'Short task name shown in Tendem lists and UIs (max 120 characters)',
		placeholder: 'Competitor research for EU freight brokerage',
	},
	{
		displayName: 'Description',
		name: 'description',
		type: 'string',
		typeOptions: { rows: 5 },
		default: '',
		required: true,
		displayOptions: showFor('task', ['create']),
		description:
			"The actual request, in plain language — posted as the first chat message. Pass the requester's own words through verbatim; Tendem's orchestrator does the scoping and asks follow-up questions over chat. Tendem declines data-scraping work by policy.",
		placeholder:
			'Research the top 5 competitors in EU freight brokerage and summarise their pricing models in a one-page brief',
	},
	{
		displayName: 'Conversation ID',
		name: 'conversationId',
		type: 'string',
		default: '',
		displayOptions: showFor('task', ['create']),
		description:
			'Optional stable identifier that lets Tendem correlate several tasks from one conversation',
	},
	{
		displayName: 'Task ID',
		name: 'taskId',
		type: 'string',
		default: '',
		required: true,
		displayOptions: showFor('task', [
			'get',
			'getContract',
			'approve',
			'cancel',
			'getResult',
			'wait',
		]),
		description: 'Task UUID returned by the Create operation',
	},
	{
		displayName:
			'This charges the Tendem account for real. Read the scope and price first with "Get Contract", put a human decision in front of this node, and pass the price through below so the confirmation names what is being bought.',
		name: 'approvalNotice',
		type: 'notice',
		default: '',
		displayOptions: showFor('task', ['approve']),
	},
	{
		displayName: 'Name',
		name: 'taskName',
		type: 'string',
		default: '',
		required: true,
		displayOptions: showFor('task', ['approve', 'cancel']),
		description: 'Task name, passed through from Get Task. Tendem uses it in its guidance text.',
	},
	{
		displayName: 'Price',
		name: 'price',
		type: 'string',
		default: '',
		required: true,
		displayOptions: showFor('task', ['approve']),
		description:
			'The formatted price from Get Task or Get Contract, e.g. "$40.00". Required by Tendem, and it means the amount being committed has to have been read before this node can approve it.',
	},
	{
		displayName: 'Confirm Spend',
		name: 'confirmSpend',
		type: 'boolean',
		default: false,
		required: true,
		displayOptions: showFor('task', ['approve']),
		description:
			'Whether to actually commit the spend. The node refuses to approve while this is off, so approval cannot happen by accident. Drive it from an expression if a human decision upstream should decide.',
	},
	{
		displayName: 'Wait for Change (Seconds)',
		name: 'waitForChangeSeconds',
		type: 'number',
		default: DEFAULT_WAIT_FOR_CHANGE_SECONDS,
		typeOptions: {
			minValue: MIN_WAIT_FOR_CHANGE_SECONDS,
			maxValue: MAX_WAIT_FOR_CHANGE_SECONDS,
		},
		displayOptions: showFor('task', ['wait']),
		description:
			'How long the Tendem server holds each request open waiting for a change (max 30, the API limit). The wait happens server-side, so this is not a client polling interval.',
	},
	{
		displayName: 'Max Rounds',
		name: 'maxRounds',
		type: 'number',
		default: DEFAULT_MAX_ROUNDS,
		typeOptions: { minValue: MIN_MAX_ROUNDS, maxValue: MAX_MAX_ROUNDS },
		displayOptions: showFor('task', ['wait']),
		description:
			'Hard cap on how many server-side waits to perform. When the budget runs out the node emits the latest snapshot with "tendemWait.timedOut" set to true rather than looping forever — branch on it and re-enter the wait later.',
	},
	{
		displayName: 'Limit',
		name: 'limit',
		type: 'number',
		default: 50,
		typeOptions: { minValue: 1, maxValue: 100 },
		displayOptions: showFor('task', ['list']),
		description: 'Max number of results to return',
	},
	{
		displayName: 'Offset',
		name: 'offset',
		type: 'number',
		default: 0,
		typeOptions: { minValue: 0 },
		displayOptions: showFor('task', ['list']),
		description: 'Number of tasks to skip',
	},
];

export const chatFields: INodeProperties[] = [
	{
		displayName: 'Task ID',
		name: 'taskId',
		type: 'string',
		default: '',
		required: true,
		displayOptions: showFor('chat', ['read', 'send']),
		description: 'Task UUID returned by the Create operation',
	},
	{
		displayName: 'From Offset',
		name: 'fromOffset',
		type: 'number',
		default: 0,
		typeOptions: { minValue: 0 },
		displayOptions: showFor('chat', ['read']),
		description: 'Offset of the first message to return. 0 returns the full history.',
	},
	{
		displayName: 'Text',
		name: 'text',
		type: 'string',
		typeOptions: { rows: 4 },
		default: '',
		required: true,
		displayOptions: showFor('chat', ['send']),
		description: 'Message text to send to the Tendem orchestrator',
	},
	{
		displayName: 'Last Seen Offset',
		name: 'lastSeenOffset',
		type: 'number',
		default: 0,
		typeOptions: { minValue: 0 },
		displayOptions: showFor('chat', ['send']),
		description:
			'The "last_seen_offset" from the previous Create, Send, or Read call. 0 for a brand-new task. If Tendem posted new content first, the response comes back with response_type "race" and the message is not delivered — re-send with the new offset.',
	},
];

export const fileFields: INodeProperties[] = [
	{
		displayName: 'Task ID',
		name: 'taskId',
		type: 'string',
		default: '',
		required: true,
		displayOptions: showFor('file', ['getUploadUrl']),
		description:
			'Task UUID to attach files to. Upload only works after the task exists, so create the task first.',
	},
	{
		displayName:
			'The returned "upload_url" is a folder URL. Append each file name to the path before the query string — <base>/<filename>?<query> — and PUT the raw bytes. Then use Chat → Send to name the files you uploaded, or Tendem will keep waiting for them.',
		name: 'uploadNotice',
		type: 'notice',
		default: '',
		displayOptions: showFor('file', ['getUploadUrl']),
	},
];
