import type {
	IAuthenticateGeneric,
	Icon,
	ICredentialTestRequest,
	ICredentialType,
	INodeProperties,
} from 'n8n-workflow';

import {
	MCP_CLIENT_NAME,
	MCP_CLIENT_VERSION,
	MCP_PROTOCOL_VERSION,
	TENDEM_DEFAULT_ENDPOINT,
} from '../nodes/Tendem/transport';

export class TendemApi implements ICredentialType {
	name = 'tendemApi';

	displayName = 'Tendem API';

	// Shares the node's icon file rather than duplicating it; resolved relative to this file,
	// which holds in dist/ too (dist/credentials -> dist/nodes/Tendem).
	// One icon, not a light/dark pair: the mark is a dark container with a white
	// glyph, so it reads on either theme, and the previous "dark" file was a
	// byte-identical copy of the light one — a duplicate posing as a variant.
	icon: Icon = 'file:../nodes/Tendem/tendem.svg';

	documentationUrl = 'https://github.com/Toloka/tendem-mcp';

	properties: INodeProperties[] = [
		{
			displayName: 'API Key',
			name: 'apiKey',
			type: 'string',
			typeOptions: { password: true },
			default: '',
			required: true,
			description:
				'Tendem API key. Create one at https://agent.tendem.ai/mcp, on the "Agent builders" tab. Sent as the "Authorization: ApiKey <token>" header.',
		},
		{
			displayName: 'MCP Endpoint',
			name: 'endpoint',
			type: 'string',
			default: TENDEM_DEFAULT_ENDPOINT,
			required: true,
			description:
				'Tendem MCP endpoint (Streamable HTTP). Change this only to target a non-production Tendem deployment.',
		},
	];

	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: {
				Authorization: '=ApiKey {{$credentials.apiKey}}',
			},
		},
	};

	/** Verifies the key by performing the MCP initialize handshake. */
	test: ICredentialTestRequest = {
		request: {
			url: '={{$credentials.endpoint}}',
			method: 'POST',
			headers: {
				Accept: 'application/json, text/event-stream',
				'Content-Type': 'application/json',
			},
			body: {
				jsonrpc: '2.0',
				id: 1,
				method: 'initialize',
				params: {
					protocolVersion: MCP_PROTOCOL_VERSION,
					capabilities: {},
					clientInfo: { name: MCP_CLIENT_NAME, version: MCP_CLIENT_VERSION },
				},
			},
		},
	};
}
