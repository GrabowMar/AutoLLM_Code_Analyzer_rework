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
	import SettingsBlock from '$lib/components/users-settings/SettingsBlock.svelte';
	import SettingsRow from '$lib/components/users-settings/SettingsRow.svelte';
	import CopyField from '$lib/components/users-settings/CopyField.svelte';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import CopyBlock from '$lib/components/inference/CopyBlock.svelte';
	import { formatApiError } from '$lib/api/core';
	import { createApiToken } from '$lib/api/tokens';
	import { fillToken, getMcpConfig, type McpConfig } from '$lib/api/integrations';
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

{#snippet tokenFooter()}
	<span class="text-[13px] text-muted-foreground">Shown once after you create it.</span>
	<Button size="sm" onclick={createToken} disabled={creating} aria-busy={creating}>
		{#if creating}<LoaderCircle class="animate-spin" />{:else}<KeyRound />{/if}
		Create token
	</Button>
{/snippet}

{#if error}
	<ErrorAlert message={error} onretry={load} />
{:else if loading}
	<LoadingState label="loading MCP configuration" />
{:else if config}
	<SettingsBlock
		title="Server"
		description="Assistants can browse models and results, start generations and analyses, and read reports."
	>
		<SettingsRow label="Server URL" description="Point your client here." stack>
			<CopyField value={config.server_url} label="MCP server URL" />
		</SettingsRow>
		<div class="settings-row grid grid-cols-2 gap-4 sm:grid-cols-4">
			{#each [{ k: 'Transport', v: 'Streamable HTTP' }, { k: 'Protocol', v: config.protocol_versions.join(', ') }, { k: 'Tools', v: String(config.tool_count) }, { k: 'Prompts', v: String(config.prompts.length) }] as fact (fact.k)}
				<div class="min-w-0">
					<p class="text-xs text-muted-foreground">{fact.k}</p>
					<p class="truncate text-sm font-medium tabular-nums" title={fact.v}>{fact.v}</p>
				</div>
			{/each}
		</div>
	</SettingsBlock>

	<SettingsBlock title="Connection token" footer={token ? undefined : tokenFooter}>
		{#snippet description()}
			Clients sign in with a personal token. Tokens embedded in a URL (claude.ai, ChatGPT) need the
			<code class="mono-tag">mcp</code> scope; <code class="mono-tag">read</code> hides every tool that changes data.
		{/snippet}
		{#if token}
			<div class="settings-row space-y-3 bg-warning/10">
				<p class="flex items-start gap-2 text-sm text-warning-fg">
					<TriangleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
					<span>Copy the token now. It is shown only once; the snippets below already contain it.</span>
				</p>
				<CopyField value={token} label="connection token" class="bg-card" />
				<div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
					<span>
						<code class="mono-tag">{tokenPrefix}…</code> · revoke it any time under
						<a href="#tokens" class="settings-link">Personal tokens</a>.
					</span>
					<Button variant="ghost" size="sm" onclick={forgetToken}><X />Hide token</Button>
				</div>
			</div>
		{:else}
			<SettingsRow label="Name" for="mcp-token-name">
				<Input id="mcp-token-name" class="sm:w-72" bind:value={tokenName} maxlength={100} autocomplete="off" />
			</SettingsRow>
			<SettingsRow label="Access" labelId="mcp-access-label" description={access === 'read' ? 'Scopes: mcp, read' : 'Scopes: mcp'}>
				<Segmented
					aria-labelledby="mcp-access-label"
					bind:value={access}
					items={[
						{ value: 'full', label: 'Full access', title: 'Scopes: mcp' },
						{ value: 'read', label: 'Read-only', title: 'Scopes: mcp, read' },
					]}
				/>
			</SettingsRow>
		{/if}
	</SettingsBlock>

	<SettingsBlock title="Set up a client">
		{#snippet description()}
			{#if token}
				The snippet includes your new token.
			{:else}
				Replace <code class="mono-tag">&lt;TOKEN&gt;</code> with a connection token, or create one above to fill it in.
			{/if}
		{/snippet}
		{#if config.clients.length === 0}
			<div class="settings-row text-sm text-muted-foreground">No client snippets available.</div>
		{:else}
			<SettingsRow label="Client" for="mcp-client">
				<div class="sm:w-72"><Select.Simple id="mcp-client" bind:value={clientId} items={clientItems} /></div>
			</SettingsRow>
			{#if client}
				<div class="settings-row space-y-2.5">
					<p class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
						<span>{client.auth === 'url' ? 'Token in the URL' : 'Token in an Authorization header'}</span>
						<span aria-hidden="true">·</span>
						<span>Goes in <span class="text-foreground">{client.location}</span></span>
						{#if client.docs_url}
							<a href={client.docs_url} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 settings-link">
								Docs<ExternalLink class="size-3" aria-hidden="true" />
							</a>
						{/if}
					</p>
					<CopyBlock text={fillToken(client.snippet, token)} label="{client.name} configuration" />
					{#if client.auth === 'url'}
						<p class="text-xs text-muted-foreground">
							The token travels in the URL: use a dedicated <code class="mono-tag">mcp</code> token and revoke it if
							the URL leaks.
						</p>
					{/if}
				</div>
			{/if}
		{/if}
	</SettingsBlock>

	<SettingsBlock>
		<details class="group">
			<summary
				class="settings-row flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium transition-colors hover:bg-muted/30 [&::-webkit-details-marker]:hidden"
			>
				<span>Tools <span class="font-normal text-muted-foreground tabular-nums">· {config.tools.length}, {readTools} read-only</span></span>
				<ChevronDown class="size-4 text-muted-foreground transition-transform duration-150 group-open:rotate-180" aria-hidden="true" />
			</summary>
			<ul class="divide-y divide-border border-t border-border">
				{#each config.tools as tool (tool.name)}
					<li class="settings-row space-y-1 py-3">
						<p class="flex flex-wrap items-center gap-2">
							<code class="font-mono text-xs">{tool.name}</code>
							<Badge variant={tool.read_only ? 'secondary' : 'outline'}>{tool.read_only ? 'read-only' : 'write'}</Badge>
						</p>
						<p class="text-xs text-muted-foreground">{tool.description}</p>
					</li>
				{/each}
			</ul>
		</details>
		{#if config.prompts.length > 0}
			<details class="group">
				<summary
					class="settings-row flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium transition-colors hover:bg-muted/30 [&::-webkit-details-marker]:hidden"
				>
					<span>Prompts <span class="font-normal text-muted-foreground tabular-nums">· {config.prompts.length}</span></span>
					<ChevronDown class="size-4 text-muted-foreground transition-transform duration-150 group-open:rotate-180" aria-hidden="true" />
				</summary>
				<ul class="flex flex-wrap gap-1.5 px-4 pb-4">
					{#each config.prompts as prompt (prompt)}
						<li><code class="mono-tag">{prompt}</code></li>
					{/each}
				</ul>
			</details>
		{/if}
	</SettingsBlock>
{/if}
