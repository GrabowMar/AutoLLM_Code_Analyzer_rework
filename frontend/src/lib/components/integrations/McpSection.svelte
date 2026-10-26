<script lang="ts">
	/**
	 * McpSection — connect MCP clients (Claude, ChatGPT, Cursor…) to LLM Lab.
	 *
	 * Loads `/integrations/mcp/config/`, lets the user mint a connection token
	 * (`POST /tokens/`) and substitutes it into every client snippet. The raw
	 * token lives only in this component's state; it is never stored.
	 */
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import CopyBlock from '$lib/components/inference/CopyBlock.svelte';
	import { formatApiError } from '$lib/api/core';
	import { createApiToken } from '$lib/api/tokens';
	import { fillToken, getMcpConfig, type McpConfig } from '$lib/api/integrations';
	import Bot from '@lucide/svelte/icons/bot';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import X from '@lucide/svelte/icons/x';

	let config = $state<McpConfig | null>(null);
	let loading = $state(true);
	let error = $state('');

	let tokenName = $state('MCP connection');
	let access = $state<'full' | 'read'>('full');
	let creating = $state(false);
	/** Raw token, shown once. Component state only. */
	let token = $state<string | null>(null);
	let tokenPrefix = $state('');

	let clientId = $state('');

	const client = $derived(config?.clients.find((c) => c.id === clientId) ?? config?.clients[0] ?? null);
	const clientItems = $derived(
		(config?.clients ?? []).map((c) => ({
			value: c.id,
			label: c.name,
			description: c.auth === 'url' ? 'Token in the URL' : 'Token in an Authorization header',
		})),
	);
	const readTools = $derived(config?.tools.filter((t) => t.read_only).length ?? 0);

	async function load() {
		loading = true;
		error = '';
		try {
			config = await getMcpConfig();
			if (!clientId && config.clients[0]) clientId = config.clients[0].id;
		} catch (e) {
			error = formatApiError(e, 'Could not load the MCP server configuration.');
		} finally {
			loading = false;
		}
	}

	async function createToken() {
		creating = true;
		try {
			const res = await createApiToken({
				name: tokenName.trim() || 'MCP connection',
				scopes: access === 'read' ? ['mcp', 'read'] : ['mcp'],
			});
			token = res.token;
			tokenPrefix = res.prefix;
			toast.success('Token created. Copy it now: it is shown only once.');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not create the token.'));
		} finally {
			creating = false;
		}
	}

	function forgetToken() {
		token = null;
		tokenPrefix = '';
	}

	onMount(load);
</script>

<div class="space-y-4">
	<Card.Root>
		<Card.Header>
			<Card.Title class="flex items-center gap-2"><Bot class="size-5" />MCP server</Card.Title>
			<Card.Description>
				Let an AI assistant (Claude, ChatGPT, Cursor, VS Code…) drive LLM Lab over the Model Context Protocol:
				browse models and results, start generations and analyses, and read reports.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			{#if error}
				<ErrorAlert message={error} onretry={load} />
			{:else if loading}
				<LoadingState label="loading MCP configuration" />
			{:else if config}
				<div class="space-y-1.5">
					<p class="section-label">Server URL</p>
					<CopyBlock text={config.server_url} label="MCP server URL" wrap />
				</div>
				<p class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
					<span>Streamable HTTP transport</span>
					<span>Protocol <span class="font-mono">{config.protocol_versions.join(', ')}</span></span>
					<span><span class="font-mono tabular-nums">{config.tool_count}</span> tools</span>
					<span><span class="font-mono tabular-nums">{config.prompts.length}</span> prompts</span>
				</p>
			{/if}
		</Card.Content>
	</Card.Root>

	{#if config}
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2"><KeyRound class="size-5" />Connection token</Card.Title>
				<Card.Description>
					Clients authenticate with a personal API token. The <code class="mono-tag">mcp</code> scope is required
					for tokens embedded in a URL (claude.ai and ChatGPT connectors); adding
					<code class="mono-tag">read</code> hides every tool that changes data.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				{#if token}
					<div class="space-y-2 rounded-md border border-warning/30 bg-warning/10 p-3">
						<p class="flex items-start gap-1.5 text-sm text-warning-fg">
							<TriangleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
							<span>
								Copy the token now: it is shown only once and is not stored in this page. The snippets below
								already contain it.
							</span>
						</p>
						<CopyBlock text={token} label="connection token" wrap />
						<div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
							<span>
								Token <code class="mono-tag">{tokenPrefix}…</code> · revoke it any time in
								<a href="/users/settings#tokens" class="underline underline-offset-2">Settings → Personal Tokens</a>.
							</span>
							<Button variant="ghost" size="sm" onclick={forgetToken}><X />Hide token</Button>
						</div>
					</div>
				{:else}
					<div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end">
						<div class="space-y-1.5">
							<Label for="mcp-token-name">Token name</Label>
							<Input id="mcp-token-name" bind:value={tokenName} maxlength={100} autocomplete="off" />
						</div>
						<div class="space-y-1.5">
							<span class="text-sm font-medium" id="mcp-access-label">Access</span>
							<Segmented
								aria-labelledby="mcp-access-label"
								bind:value={access}
								items={[
									{ value: 'full', label: 'Full access', title: 'Scopes: mcp' },
									{ value: 'read', label: 'Read-only', title: 'Scopes: mcp, read' },
								]}
							/>
						</div>
					</div>
					<div class="flex flex-wrap items-center justify-between gap-2">
						<p class="text-xs text-muted-foreground">
							Scopes: <code class="mono-tag">{access === 'read' ? 'mcp, read' : 'mcp'}</code>
						</p>
						<Button size="sm" onclick={createToken} disabled={creating} aria-busy={creating}>
							{#if creating}<LoaderCircle class="animate-spin" />{:else}<KeyRound />{/if}
							Create connection token
						</Button>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Set up a client</Card.Title>
				<Card.Description>
					{#if token}
						Snippets include your new token.
					{:else}
						Replace <code class="mono-tag">&lt;TOKEN&gt;</code> with a connection token, or create one above to fill
						it in.
					{/if}
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				{#if config.clients.length === 0}
					<p class="text-sm text-muted-foreground">No client snippets available.</p>
				{:else}
					<div class="max-w-md space-y-1.5">
						<Label for="mcp-client">Client</Label>
						<Select.Simple id="mcp-client" bind:value={clientId} items={clientItems} />
					</div>
					{#if client}
						<div class="space-y-2">
							<p class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
								<Badge variant="outline">{client.auth === 'url' ? 'Token in URL' : 'Header auth'}</Badge>
								<span>Where: <span class="text-foreground">{client.location}</span></span>
								{#if client.docs_url}
									<a
										href={client.docs_url}
										target="_blank"
										rel="noopener noreferrer"
										class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
									>
										Docs<ExternalLink class="size-3" aria-hidden="true" />
									</a>
								{/if}
							</p>
							<CopyBlock text={fillToken(client.snippet, token)} label="{client.name} configuration" />
							{#if client.auth === 'url'}
								<p class="text-xs text-muted-foreground">
									The token travels in the URL: use a dedicated token with the <code class="mono-tag">mcp</code>
									scope and revoke it if the URL leaks.
								</p>
							{/if}
						</div>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Content class="space-y-2">
				<details class="group">
					<summary class="flex cursor-pointer list-none items-center justify-between gap-2 py-1 text-sm font-medium">
						<span>
							Tools <span class="font-mono text-xs text-muted-foreground tabular-nums"
								>({config.tools.length} · {readTools} read-only)</span
							>
						</span>
						<ChevronDown class="size-4 transition-transform duration-150 group-open:rotate-180" aria-hidden="true" />
					</summary>
					<ul class="mt-2 divide-y divide-border">
						{#each config.tools as tool (tool.name)}
							<li class="flex flex-wrap items-start gap-x-3 gap-y-1 py-2">
								<code class="font-mono text-xs">{tool.name}</code>
								<Badge variant={tool.read_only ? 'secondary' : 'outline'}>{tool.read_only ? 'read-only' : 'write'}</Badge>
								<p class="w-full text-xs text-muted-foreground">{tool.description}</p>
							</li>
						{/each}
					</ul>
				</details>
				{#if config.prompts.length > 0}
					<details class="group border-t border-border pt-2">
						<summary class="flex cursor-pointer list-none items-center justify-between gap-2 py-1 text-sm font-medium">
							<span>
								Prompts <span class="font-mono text-xs text-muted-foreground tabular-nums">({config.prompts.length})</span>
							</span>
							<ChevronDown class="size-4 transition-transform duration-150 group-open:rotate-180" aria-hidden="true" />
						</summary>
						<ul class="mt-2 flex flex-wrap gap-1.5">
							{#each config.prompts as prompt (prompt)}
								<li><code class="mono-tag">{prompt}</code></li>
							{/each}
						</ul>
					</details>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
