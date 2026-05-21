<script lang="ts">
	import { listApiTokens, createApiToken, revokeApiToken } from '$lib/api/client';
	import type { ApiTokenSummary, ApiTokenCreatedResponse, CreateApiTokenPayload } from '$lib/api/client';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Separator } from '$lib/components/ui/separator';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Copy from '@lucide/svelte/icons/copy';
	import Key from '@lucide/svelte/icons/key';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Plus from '@lucide/svelte/icons/plus';
	import AlertTriangle from '@lucide/svelte/icons/triangle-alert';

	interface Props {}

	let {}: Props = $props();

	let tokens = $state<ApiTokenSummary[]>([]);
	let newlyCreatedToken = $state<ApiTokenCreatedResponse | null>(null);
	let tokensLoading = $state(true);
	let tokenActionLoading = $state(false);
	let showCreateDialog = $state(false);
	let newTokenName = $state('');
	let newTokenScopes = $state<string[]>([]);
	let newTokenExpiry = $state('');

	onMount(() => {
		listApiTokens()
			.then((list) => {
				tokens = list;
			})
			.catch(() => {})
			.finally(() => {
				tokensLoading = false;
			});
	});

	async function handleCreateToken() {
		if (!newTokenName.trim()) {
			toast.error('Token name is required');
			return;
		}
		tokenActionLoading = true;
		try {
			const payload: CreateApiTokenPayload = {
				name: newTokenName.trim(),
				scopes: newTokenScopes,
				expires_at: newTokenExpiry || null,
			};
			const created = await createApiToken(payload);
			newlyCreatedToken = created;
			tokens = [created, ...tokens];
			showCreateDialog = false;
			newTokenName = '';
			newTokenScopes = [];
			newTokenExpiry = '';
			toast.success('API token created');
		} catch {
			toast.error('Failed to create token');
		} finally {
			tokenActionLoading = false;
		}
	}

	async function handleRevokeToken(id: string) {
		tokenActionLoading = true;
		try {
			await revokeApiToken(id);
			tokens = tokens.map((t) =>
				t.id === id ? { ...t, revoked_at: new Date().toISOString() } : t
			);
			if (newlyCreatedToken?.id === id) newlyCreatedToken = null;
			toast.success('API token revoked');
		} catch {
			toast.error('Failed to revoke token');
		} finally {
			tokenActionLoading = false;
		}
	}

	async function copyToken(token: string) {
		try {
			await navigator.clipboard.writeText(token);
			toast.success('Token copied to clipboard');
		} catch {
			toast.error('Failed to copy token');
		}
	}
</script>

<!-- New token banner (shown once after creation) -->
{#if newlyCreatedToken}
	<Alert class="border-amber-400/60 bg-amber-50 dark:bg-amber-950/30">
		<AlertTriangle class="h-4 w-4 text-amber-600" />
		<AlertDescription class="space-y-3">
			<p class="font-semibold text-amber-800 dark:text-amber-300">
				Save this token — it will not be shown again.
			</p>
			<div class="flex items-center gap-2 rounded-lg border border-amber-300 bg-white dark:bg-zinc-900 p-3">
				<code class="flex-1 break-all text-sm font-mono">{newlyCreatedToken.token}</code>
				<Button
					size="icon"
					variant="ghost"
					onclick={() => copyToken(newlyCreatedToken!.token)}
					title="Copy token"
				>
					<Copy class="h-4 w-4" />
				</Button>
			</div>
			<Button
				variant="ghost"
				size="sm"
				onclick={() => (newlyCreatedToken = null)}
			>
				Dismiss
			</Button>
		</AlertDescription>
	</Alert>
{/if}

<!-- API Tokens list -->
<Card.Root>
	<Card.Header class="flex flex-row items-center justify-between space-y-0">
		<div>
			<Card.Title class="flex items-center gap-2">
				<Key class="h-5 w-5" />
				API Tokens
			</Card.Title>
			<Card.Description>Personal access tokens for programmatic API access.</Card.Description>
		</div>
		<Button onclick={() => (showCreateDialog = true)} size="sm">
			<Plus class="h-4 w-4" />
			New Token
		</Button>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if tokensLoading}
			<div class="space-y-2">
				{#each [1, 2] as _}
					<div class="h-14 animate-pulse rounded-md bg-muted"></div>
				{/each}
			</div>
		{:else if tokens.length === 0}
			<div class="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border p-8 text-center">
				<Key class="h-8 w-8 text-muted-foreground" />
				<p class="text-sm text-muted-foreground">No API tokens yet. Create one to get started.</p>
			</div>
		{:else}
			{#each tokens as token (token.id)}
				<div class="flex flex-col gap-2 rounded-lg border border-border p-3 sm:flex-row sm:items-center sm:justify-between {token.revoked_at ? 'opacity-50' : ''}">
					<div class="min-w-0 space-y-0.5">
						<div class="flex items-center gap-2 flex-wrap">
							<p class="text-sm font-medium truncate">{token.name}</p>
							{#if token.revoked_at}
								<span class="rounded-full bg-destructive/10 px-2 py-0.5 text-xs text-destructive">Revoked</span>
							{:else if token.expires_at && new Date(token.expires_at) < new Date()}
								<span class="rounded-full bg-amber-100 dark:bg-amber-900/30 px-2 py-0.5 text-xs text-amber-700 dark:text-amber-400">Expired</span>
							{:else}
								<span class="rounded-full bg-green-100 dark:bg-green-900/30 px-2 py-0.5 text-xs text-green-700 dark:text-green-400">Active</span>
							{/if}
							{#each token.scopes as scope}
								<span class="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground">{scope}</span>
							{/each}
						</div>
						<div class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
							<span class="font-mono">{token.prefix}…</span>
							<span>Created {new Date(token.created_at).toLocaleDateString()}</span>
							{#if token.last_used_at}
								<span>Last used {new Date(token.last_used_at).toLocaleDateString()}</span>
							{/if}
							{#if token.expires_at}
								<span>Expires {new Date(token.expires_at).toLocaleDateString()}</span>
							{/if}
						</div>
					</div>
					{#if !token.revoked_at}
						<Button
							variant="destructive"
							size="sm"
							disabled={tokenActionLoading}
							onclick={() => handleRevokeToken(token.id)}
						>
							<Trash2 class="h-4 w-4" />
							Revoke
						</Button>
					{/if}
				</div>
			{/each}
		{/if}
	</Card.Content>
</Card.Root>

<!-- Create Token Dialog (inline) -->
{#if showCreateDialog}
	<div
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
		role="dialog"
		aria-modal="true"
	>
		<Card.Root class="w-full max-w-md shadow-xl">
			<Card.Header>
				<Card.Title>Create API Token</Card.Title>
				<Card.Description>Generate a token for programmatic access to the API.</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-2">
					<Label for="token-name">Token Name *</Label>
					<Input
						id="token-name"
						placeholder="e.g. CI/CD pipeline"
						bind:value={newTokenName}
						autofocus
					/>
				</div>
				<div class="space-y-2">
					<Label>Scopes (optional)</Label>
					<div class="flex flex-wrap gap-2">
						{#each ['read', 'write', 'admin'] as scope}
							<label class="flex cursor-pointer items-center gap-1.5 rounded-md border border-border px-2.5 py-1.5 text-sm hover:bg-muted {newTokenScopes.includes(scope) ? 'border-primary bg-primary/5' : ''}">
								<input
									type="checkbox"
									class="sr-only"
									checked={newTokenScopes.includes(scope)}
									onchange={(e) => {
										if (e.currentTarget.checked) {
											newTokenScopes = [...newTokenScopes, scope];
										} else {
											newTokenScopes = newTokenScopes.filter((s) => s !== scope);
										}
									}}
								/>
								{scope}
							</label>
						{/each}
					</div>
				</div>
				<div class="space-y-2">
					<Label for="token-expiry">Expiry Date (optional)</Label>
					<Input
						id="token-expiry"
						type="date"
						bind:value={newTokenExpiry}
						min={new Date().toISOString().slice(0, 10)}
					/>
				</div>
			</Card.Content>
			<div class="flex justify-end gap-2 border-t border-border p-4">
				<Button
					variant="outline"
					onclick={() => {
						showCreateDialog = false;
						newTokenName = '';
						newTokenScopes = [];
						newTokenExpiry = '';
					}}
				>
					Cancel
				</Button>
				<Button
					disabled={tokenActionLoading || !newTokenName.trim()}
					onclick={handleCreateToken}
				>
					{tokenActionLoading ? 'Creating…' : 'Create Token'}
				</Button>
			</div>
		</Card.Root>
	</div>
{/if}

<!-- Usage Guide -->
<Card.Root>
	<Card.Header>
		<Card.Title>Usage Guide</Card.Title>
		<Card.Description>How to use the API with your token.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		<div class="space-y-2">
			<Label>Example Request</Label>
			<pre class="overflow-x-auto rounded-lg border border-border bg-muted/50 p-3 text-xs sm:text-sm font-mono">curl -H "Authorization: Bearer YOUR_TOKEN" \
  https://your-domain.com/api/models/</pre>
		</div>

		<Separator />

		<div class="space-y-2">
			<Label>Available Endpoints</Label>
			<div class="overflow-x-auto rounded-lg border border-border">
				<table class="w-full text-sm">
					<thead>
						<tr class="border-b border-border bg-muted/50">
							<th class="px-3 py-2 text-left font-medium">Method</th>
							<th class="px-3 py-2 text-left font-medium">Endpoint</th>
							<th class="px-3 py-2 text-left font-medium">Description</th>
						</tr>
					</thead>
					<tbody>
						<tr class="border-b border-border">
							<td class="px-3 py-2"><code class="rounded bg-muted px-1 text-xs">GET</code></td>
							<td class="px-3 py-2 font-mono text-xs">/api/models/</td>
							<td class="px-3 py-2 text-muted-foreground">List all AI models</td>
						</tr>
						<tr class="border-b border-border">
							<td class="px-3 py-2"><code class="rounded bg-muted px-1 text-xs">GET</code></td>
							<td class="px-3 py-2 font-mono text-xs">/api/models/stats/</td>
							<td class="px-3 py-2 text-muted-foreground">Model statistics</td>
						</tr>
						<tr class="border-b border-border">
							<td class="px-3 py-2"><code class="rounded bg-muted px-1 text-xs">POST</code></td>
							<td class="px-3 py-2 font-mono text-xs">/api/models/sync/</td>
							<td class="px-3 py-2 text-muted-foreground">Sync from OpenRouter</td>
						</tr>
						<tr>
							<td class="px-3 py-2"><code class="rounded bg-muted px-1 text-xs">GET</code></td>
							<td class="px-3 py-2 font-mono text-xs">/api/users/me/</td>
							<td class="px-3 py-2 text-muted-foreground">Current user profile</td>
						</tr>
					</tbody>
				</table>
			</div>
		</div>
	</Card.Content>
</Card.Root>
