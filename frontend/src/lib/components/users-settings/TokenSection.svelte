<script lang="ts">
	/**
	 * TokenSection — personal bearer tokens: list, create (shown once), revoke.
	 * Scopes only matter to the MCP server (`mcp`, `read`); on the REST API a
	 * token acts with the full rights of its owner.
	 */
	import { listApiTokens, createApiToken, revokeApiToken } from '$lib/api/tokens';
	import type { ApiTokenSummary, ApiTokenCreatedResponse } from '$lib/api/tokens';
	import SettingsBlock from './SettingsBlock.svelte';
	import CopyField from './CopyField.svelte';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import CopyBlock from '$lib/components/inference/CopyBlock.svelte';
	import { useSectionStatus } from './section-status.svelte';
	import { formatApiError } from '$lib/api/core';
	import { formatAgo } from '$lib/components/inference/endpoint-display';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Terminal from '@lucide/svelte/icons/terminal';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	const SCOPES = [
		{ value: 'mcp', label: 'mcp', help: 'Allows the token inside an MCP connector URL (claude.ai, ChatGPT).' },
		{ value: 'read', label: 'read', help: 'MCP clients see only tools that do not change data.' },
	];
	const EXPIRY = [
		{ value: '30', label: '30 days' },
		{ value: '90', label: '90 days' },
		{ value: '365', label: '1 year' },
		{ value: 'never', label: 'Never' },
	];

	let tokens = $state<ApiTokenSummary[]>([]);
	let loading = $state(true);
	let error = $state('');
	let created = $state<ApiTokenCreatedResponse | null>(null);
	let showRevoked = $state(false);

	let createOpen = $state(false);
	let creating = $state(false);
	let name = $state('');
	let scopes = $state<string[]>([]);
	let expiry = $state('90');

	let revoking = $state<ApiTokenSummary | null>(null);
	let revokeOpen = $state(false);
	let revokeBusy = $state(false);

	const now = Date.now();
	const isExpired = (t: ApiTokenSummary) => t.expires_at != null && Date.parse(t.expires_at) < now;
	const active = $derived(tokens.filter((t) => !t.revoked_at));
	const revoked = $derived(tokens.filter((t) => t.revoked_at));
	const visible = $derived(showRevoked ? tokens : active);
	const origin = typeof window === 'undefined' ? '' : window.location.origin;

	const report = useSectionStatus('tokens');
	$effect(() => {
		if (loading || error) return;
		const n = active.filter((t) => !isExpired(t)).length;
		report(n ? { tone: 'neutral', label: `${n} active` } : null);
	});

	async function load() {
		error = '';
		try {
			tokens = await listApiTokens();
		} catch (e) {
			error = formatApiError(e, 'Could not load your tokens.');
		} finally {
			loading = false;
		}
	}

	function openCreate() {
		name = '';
		scopes = [];
		expiry = '90';
		createOpen = true;
	}

	function expiresAt(): string | null {
		if (expiry === 'never') return null;
		return new Date(Date.now() + Number(expiry) * 86_400_000).toISOString();
	}

	async function create(e: Event) {
		e.preventDefault();
		if (!name.trim()) return;
		creating = true;
		try {
			const res = await createApiToken({ name: name.trim(), scopes, expires_at: expiresAt() });
			created = res;
			tokens = [res, ...tokens];
			createOpen = false;
		} catch (err) {
			toast.error(formatApiError(err, 'Could not create the token.'));
		} finally {
			creating = false;
		}
	}

	function askRevoke(t: ApiTokenSummary) {
		revoking = t;
		revokeOpen = true;
	}

	async function revoke() {
		if (!revoking) return;
		const id = revoking.id;
		revokeBusy = true;
		try {
			await revokeApiToken(id);
			tokens = tokens.map((t) => (t.id === id ? { ...t, revoked_at: new Date().toISOString() } : t));
			if (created?.id === id) created = null;
			revokeOpen = false;
			toast.success('Token revoked.');
		} catch (err) {
			toast.error(formatApiError(err, 'Could not revoke the token.'));
		} finally {
			revokeBusy = false;
		}
	}

	function toggleScope(scope: string, on: boolean) {
		scopes = on ? [...scopes, scope] : scopes.filter((s) => s !== scope);
	}

	onMount(load);
</script>

{#if created}
	<div class="settings-row space-y-3 bg-warning/10">
		<p class="flex items-start gap-2 text-sm text-warning-fg">
			<TriangleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
			<span>Copy <span class="font-medium">{created.name}</span> now. It is shown only once.</span>
		</p>
		<CopyField value={created.token} label="new API token" class="bg-card" />
		<div class="flex justify-end">
			<Button variant="ghost" size="sm" onclick={() => (created = null)}><X />Done, I've copied it</Button>
		</div>
	</div>
{/if}

<SettingsBlock title="Tokens">
	{#snippet description()}
		<span class="tabular-nums">{active.length}</span> active{#if revoked.length}
			· <span class="tabular-nums">{revoked.length}</span> revoked{/if}
	{/snippet}
	{#snippet actions()}
		{#if revoked.length}
			<Button variant="ghost" size="sm" onclick={() => (showRevoked = !showRevoked)} aria-pressed={showRevoked}>
				{showRevoked ? 'Hide revoked' : 'Show revoked'}
			</Button>
		{/if}
		<Button size="sm" onclick={openCreate}><Plus />New token</Button>
	{/snippet}

	{#if error}
		<div class="settings-row"><ErrorAlert message={error} onretry={load} /></div>
	{:else if loading}
		<LoadingState label="loading tokens" />
	{:else if visible.length === 0}
		<EmptyState
			compact
			icon={Terminal}
			title="No active tokens"
			description="Create one for a script, CI job or MCP client, and revoke it when it is no longer needed."
		/>
	{:else}
		{#each visible as t (t.id)}
			<div class="settings-row flex items-center justify-between gap-4 {t.revoked_at ? 'opacity-60' : ''}">
				<div class="min-w-0 space-y-1">
					<p class="flex flex-wrap items-center gap-2 text-sm">
						<span class="truncate font-medium">{t.name}</span>
						{#if t.revoked_at}
							<StatusBadge status="revoked" tone="neutral" />
						{:else if isExpired(t)}
							<StatusBadge status="expired" tone="warning" />
						{/if}
						{#each t.scopes as scope (scope)}<code class="mono-tag">{scope}</code>{/each}
					</p>
					<p class="flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
						<code class="font-mono">{t.prefix}…</code>
						<span aria-hidden="true">·</span>
						<span>created {formatAgo(t.created_at)}</span>
						<span aria-hidden="true">·</span>
						<span>{t.last_used_at ? `used ${formatAgo(t.last_used_at)}` : 'never used'}</span>
						{#if t.expires_at && !t.revoked_at}
							<span aria-hidden="true">·</span>
							<span>{isExpired(t) ? 'expired' : 'expires'} {new Date(t.expires_at).toLocaleDateString()}</span>
						{/if}
					</p>
				</div>
				{#if !t.revoked_at}
					<Button
						variant="ghost"
						size="sm"
						class="shrink-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
						onclick={() => askRevoke(t)}
					>
						Revoke
					</Button>
				{/if}
			</div>
		{/each}
	{/if}
</SettingsBlock>

<SettingsBlock title="Using a token" description="Send it as a bearer token. It has your full access on the REST API, so keep it secret.">
	<div class="settings-row space-y-3">
		<CopyBlock text={`curl -H "Authorization: Bearer <TOKEN>" \\\n  ${origin}/api/users/me/`} label="example request" />
		<p class="text-[13px] leading-5 text-muted-foreground">
			Every endpoint is in the <a href="/docs/api-reference" class="settings-link">API reference</a>. For AI
			assistants, use the <a href="#mcp" class="settings-link">MCP server</a> instead.
		</p>
	</div>
</SettingsBlock>

<Dialog.Root bind:open={createOpen}>
	<Dialog.Content class="max-w-md">
		<form onsubmit={create} class="space-y-5">
			<Dialog.Header>
				<Dialog.Title>New personal token</Dialog.Title>
				<Dialog.Description>The token is shown once after you create it.</Dialog.Description>
			</Dialog.Header>
			<div class="space-y-1.5">
				<Label for="token-name">Name</Label>
				<Input id="token-name" placeholder="e.g. CI pipeline" bind:value={name} maxlength={100} autocomplete="off" />
			</div>
			<div class="space-y-1.5">
				<span class="text-sm font-medium" id="token-expiry-label">Expires</span>
				<Segmented aria-labelledby="token-expiry-label" bind:value={expiry} items={EXPIRY} fullWidth size="sm" />
			</div>
			<fieldset class="space-y-2">
				<legend class="text-sm font-medium">MCP scopes <span class="font-normal text-muted-foreground">(optional)</span></legend>
				{#each SCOPES as s (s.value)}
					<label class="flex items-start gap-2.5 text-sm">
						<Checkbox
							class="mt-0.5"
							checked={scopes.includes(s.value)}
							onCheckedChange={(v) => toggleScope(s.value, v === true)}
						/>
						<span>
							<code class="mono-tag">{s.label}</code>
							<span class="mt-0.5 block text-xs text-muted-foreground">{s.help}</span>
						</span>
					</label>
				{/each}
			</fieldset>
			<Dialog.Footer>
				<Button type="button" variant="outline" size="sm" onclick={() => (createOpen = false)} disabled={creating}>
					Cancel
				</Button>
				<Button type="submit" size="sm" disabled={creating || !name.trim()} aria-busy={creating}>
					{#if creating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
					Create token
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={revokeOpen}
	title="Revoke {revoking?.name ?? 'token'}?"
	description="Anything using this token stops working immediately. This cannot be undone."
	confirmLabel="Revoke"
	destructive
	busy={revokeBusy}
	onConfirm={revoke}
/>
