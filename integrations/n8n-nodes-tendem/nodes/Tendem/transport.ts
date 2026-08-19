import type { IDataObject } from 'n8n-workflow';

/**
 * Minimal MCP Streamable HTTP client.
 *
 * Deliberately dependency-free: n8n's verified-community-node guidelines forbid runtime
 * dependencies, so this speaks JSON-RPC over HTTP directly instead of pulling in an MCP SDK.
 * All I/O goes through an injected requester, which in the node is
 * `this.helpers.httpRequestWithAuthentication` so the credential — never this module —
 * owns the API key.
 *
 * Spec: https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
 */

export const MCP_PROTOCOL_VERSION = '2025-06-18';
export const MCP_CLIENT_NAME = 'n8n-nodes-tendem';
export const MCP_CLIENT_VERSION = '0.1.2';

/** Default Tendem MCP endpoint, including the n8n attribution hash. */
export const TENDEM_DEFAULT_ENDPOINT = 'https://mcp.tendem.ai/mcp?utm_hash=83dad40a52';

export interface McpHttpRequestOptions {
	method: 'POST';
	url: string;
	headers: Record<string, string>;
	body: IDataObject;
	json: boolean;
	returnFullResponse: true;
	ignoreHttpStatusErrors: true;
	timeout: number;
}

export interface McpHttpResponse {
	statusCode: number;
	headers?: Record<string, unknown>;
	body?: unknown;
}

export type McpHttpRequester = (options: McpHttpRequestOptions) => Promise<McpHttpResponse>;

export class McpError extends Error {
	readonly code?: number;

	readonly data?: unknown;

	/** HTTP status of the failing response, when the failure was an HTTP-level one. */
	readonly httpStatus?: number;

	constructor(message: string, code?: number, data?: unknown, httpStatus?: number) {
		super(message);
		this.name = 'McpError';
		this.code = code;
		this.data = data;
		this.httpStatus = httpStatus;
	}
}

/** Raised when the server reports the MCP session is gone (HTTP 404 on a session request). */
export class McpSessionExpiredError extends McpError {
	constructor() {
		super('The Tendem MCP session expired');
		this.name = 'McpSessionExpiredError';
	}
}

function headerValue(headers: Record<string, unknown> | undefined, name: string): string | undefined {
	if (!headers) return undefined;
	const wanted = name.toLowerCase();
	for (const [key, value] of Object.entries(headers)) {
		if (key.toLowerCase() !== wanted) continue;
		if (Array.isArray(value)) return value.length > 0 ? String(value[0]) : undefined;
		if (value === undefined || value === null) return undefined;
		return String(value);
	}
	return undefined;
}

function tryParseJson(text: string): unknown {
	try {
		return JSON.parse(text);
	} catch {
		return undefined;
	}
}

/**
 * Pulls the JSON-RPC message out of a response body. The server may answer a POST with either
 * `application/json` (one object) or `text/event-stream` (SSE), and clients must support both.
 */
export function extractJsonRpcMessage(body: unknown): IDataObject | undefined {
	if (body === undefined || body === null) return undefined;
	if (typeof body === 'object') return body as IDataObject;
	if (typeof body !== 'string') return undefined;

	const text = body.trim();
	if (text === '') return undefined;

	if (text.startsWith('{') || text.startsWith('[')) {
		const parsed = tryParseJson(text);
		if (parsed && typeof parsed === 'object') return parsed as IDataObject;
	}

	return extractFromSse(text);
}

function extractFromSse(text: string): IDataObject | undefined {
	let last: IDataObject | undefined;
	let buffer: string[] = [];

	const flush = (): void => {
		if (buffer.length === 0) return;
		const payload = buffer.join('\n').trim();
		buffer = [];
		if (payload === '' || payload === '[DONE]') return;
		const parsed = tryParseJson(payload);
		if (!parsed || typeof parsed !== 'object') return;
		const message = parsed as IDataObject;
		// Ignore server-initiated notifications; keep only the actual response.
		if (message.result !== undefined || message.error !== undefined) last = message;
	};

	for (const line of text.split(/\r?\n/)) {
		if (line === '') {
			flush();
			continue;
		}
		if (line.startsWith(':')) continue;
		const separator = line.indexOf(':');
		const field = separator === -1 ? line : line.slice(0, separator);
		let value = separator === -1 ? '' : line.slice(separator + 1);
		if (value.startsWith(' ')) value = value.slice(1);
		if (field === 'data') buffer.push(value);
	}
	flush();

	return last;
}

function collectText(content: unknown): string {
	if (!Array.isArray(content)) return '';
	const parts: string[] = [];
	for (const entry of content) {
		if (!entry || typeof entry !== 'object') continue;
		const part = entry as IDataObject;
		if (part.type === 'text' && typeof part.text === 'string') parts.push(part.text);
	}
	return parts.join('\n').trim();
}

export function toolResultIsError(result: unknown): boolean {
	if (!result || typeof result !== 'object') return false;
	return (result as IDataObject).isError === true;
}

/**
 * Turns an MCP `tools/call` result into a flat object suitable for an n8n item.
 * Tendem returns JSON inside a text content block, so parse that when present.
 */
export function unwrapToolResult(result: unknown): IDataObject {
	if (!result || typeof result !== 'object' || Array.isArray(result)) {
		return { result: result === undefined ? null : (result as never) };
	}

	const envelope = result as IDataObject;

	const structured = envelope.structuredContent;
	if (structured && typeof structured === 'object' && !Array.isArray(structured)) {
		return structured as IDataObject;
	}

	const text = collectText(envelope.content);
	if (text !== '') {
		const parsed = tryParseJson(text);
		if (Array.isArray(parsed)) return { items: parsed };
		if (parsed && typeof parsed === 'object') return parsed as IDataObject;
		return { text };
	}

	return envelope;
}

export interface McpSessionOptions {
	endpoint: string;
	timeoutMs?: number;
}

export class McpSession {
	private readonly request: McpHttpRequester;

	private readonly endpoint: string;

	private readonly timeoutMs: number;

	private sessionId?: string;

	private protocolVersion: string = MCP_PROTOCOL_VERSION;

	private nextId = 0;

	private handshake?: Promise<void>;

	constructor(request: McpHttpRequester, options: McpSessionOptions) {
		this.request = request;
		this.endpoint = options.endpoint;
		this.timeoutMs = options.timeoutMs ?? 120_000;
	}

	async callTool(name: string, args: IDataObject = {}): Promise<IDataObject> {
		await this.ensureInitialized();

		// Settled-outcome rather than try/catch: the caller (the node) is the layer that owns turning
		// failures into n8n node errors, so this module must not swallow, wrap, or re-decorate them.
		const outcome = await this.invokeTool(name, args).then(
			(value) => ({ ok: true as const, value }),
			(error: unknown) => ({ ok: false as const, error }),
		);

		if (outcome.ok) return outcome.value;

		if (outcome.error instanceof McpSessionExpiredError) {
			// Spec: on 404 for a session request, start a fresh session and retry once.
			this.reset();
			await this.ensureInitialized();
			return await this.invokeTool(name, args);
		}

		throw outcome.error;
	}

	private reset(): void {
		this.sessionId = undefined;
		this.handshake = undefined;
		this.protocolVersion = MCP_PROTOCOL_VERSION;
	}

	private async invokeTool(name: string, args: IDataObject): Promise<IDataObject> {
		const result = (await this.rpc('tools/call', { name, arguments: args })) ?? {};
		if (toolResultIsError(result)) {
			const message = collectText((result as IDataObject).content) || `Tendem tool "${name}" failed`;
			throw new McpError(message);
		}
		return unwrapToolResult(result);
	}

	private async ensureInitialized(): Promise<void> {
		if (this.handshake === undefined) {
			this.handshake = this.performHandshake().catch((error) => {
				this.handshake = undefined;
				throw error;
			});
		}
		await this.handshake;
	}

	private async performHandshake(): Promise<void> {
		const response = await this.post(
			{
				jsonrpc: '2.0',
				id: (this.nextId += 1),
				method: 'initialize',
				params: {
					protocolVersion: MCP_PROTOCOL_VERSION,
					capabilities: {},
					clientInfo: { name: MCP_CLIENT_NAME, version: MCP_CLIENT_VERSION },
				},
			},
			false,
		);

		const result = this.handleResponse('initialize', response, false) ?? {};

		const negotiated = result.protocolVersion;
		if (typeof negotiated === 'string' && negotiated !== '') this.protocolVersion = negotiated;

		const sessionId = headerValue(response.headers, 'mcp-session-id');
		if (sessionId !== undefined && sessionId !== '') this.sessionId = sessionId;

		const ack = await this.post({ jsonrpc: '2.0', method: 'notifications/initialized' }, true);
		this.handleResponse('notifications/initialized', ack, true);
	}

	private async rpc(method: string, params: IDataObject): Promise<IDataObject | undefined> {
		const response = await this.post(
			{ jsonrpc: '2.0', id: (this.nextId += 1), method, params },
			true,
		);
		return this.handleResponse(method, response, false);
	}

	private async post(body: IDataObject, includeProtocolVersion: boolean): Promise<McpHttpResponse> {
		return await this.request({
			method: 'POST',
			url: this.endpoint,
			headers: this.buildHeaders(includeProtocolVersion),
			body,
			json: true,
			returnFullResponse: true,
			ignoreHttpStatusErrors: true,
			timeout: this.timeoutMs,
		});
	}

	private buildHeaders(includeProtocolVersion: boolean): Record<string, string> {
		const headers: Record<string, string> = {
			'Content-Type': 'application/json',
			Accept: 'application/json, text/event-stream',
		};
		if (this.sessionId !== undefined) headers['Mcp-Session-Id'] = this.sessionId;
		if (includeProtocolVersion) headers['MCP-Protocol-Version'] = this.protocolVersion;
		return headers;
	}

	private handleResponse(
		method: string,
		response: McpHttpResponse,
		isNotification: boolean,
	): IDataObject | undefined {
		const status = response.statusCode;

		if (status === 404 && this.sessionId !== undefined) throw new McpSessionExpiredError();

		const message = extractJsonRpcMessage(response.body);

		if (status < 200 || status >= 300) {
			const rpcError = (message?.error ?? undefined) as IDataObject | undefined;
			const detail =
				rpcError !== undefined
					? String(rpcError.message ?? '')
					: typeof response.body === 'string'
						? response.body.slice(0, 300)
						: '';
			const hint =
				status === 401 || status === 403
					? ' Check the API key on the Tendem credential (create one at https://agent.tendem.ai/mcp, "Agent builders" tab).'
					: '';
			throw new McpError(
				`Tendem MCP "${method}" failed (HTTP ${status})${detail !== '' ? `: ${detail}` : ''}.${hint}`,
				typeof rpcError?.code === 'number' ? rpcError.code : undefined,
				rpcError?.data,
				status,
			);
		}

		if (isNotification) return undefined;

		if (message === undefined) {
			throw new McpError(`Tendem MCP returned an empty response for "${method}"`);
		}

		if (message.error !== undefined && message.error !== null) {
			const rpcError = message.error as IDataObject;
			throw new McpError(
				String(rpcError.message ?? `Tendem MCP "${method}" returned an error`),
				typeof rpcError.code === 'number' ? rpcError.code : undefined,
				rpcError.data,
			);
		}

		return (message.result ?? undefined) as IDataObject | undefined;
	}
}
