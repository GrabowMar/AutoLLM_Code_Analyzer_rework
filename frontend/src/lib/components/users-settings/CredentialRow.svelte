<script lang="ts">
	/**
	 * CredentialRow — one personal provider key (OpenRouter, Hugging Face,
	 * GitHub) as a row inside a `SettingsBlock`: status, test, replace and
	 * remove, with the key form opening inline. Keys are validated before they
	 * are stored and are never returned by the API.
	 */
	import {
		getCredentialStatus,
		setCredentialKey,
		deleteCredentialKey,
		testCredentialKey,
		type CredentialProvider,
		type CredentialStatus,
	} from '$lib/api/credentials';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatAgo } from '$lib/components/inference/endpoint-display';
	import type { Tone } from '$lib/constants/colors';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Plus from '@lucide/svelte/icons/plus';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	interface Props {
		provider: CredentialProvider;
		title: string;
		description: string;
		signupUrl: string;
		signupLabel: string;
		placeholder: string;
		inputId: string;
		/** Called after the stored key was saved, removed or re-tested. */
		onchange?: () => void;
		/** Receives the stored-key status whenever it is (re)loaded. */
		onstatus?: (status: CredentialStatus | null) => void;
	}

	let { provider, title, description, signupUrl, signupLabel, placeholder, inputId, onchange, onstatus }: Props = $props();

	let status = $state<CredentialStatus | null>(null);
	let loading = $state(true);
	let saving = $state(false);
	let testing = $state(false);
	let replacing = $state(false);
	let removeOpen = $state(false);
	let newKey = $state('');

	const VALIDATION: Record<string, { label: string; tone: Tone }> = {
		valid: { label: 'valid', tone: 'success' },
		invalid: { label: 'invalid', tone: 'danger' },
		rate_limited: { label: 'rate limited', tone: 'warning' },
		network_error: { label: 'network error', tone: 'warning' },
	};
	const validation = $derived(
		VALIDATION[status?.last_validation_status ?? ''] ?? { label: 'not validated', tone: 'neutral' as Tone },
	);

	$effect(() => {
		if (!loading) onstatus?.(status);
	});

	onMount(refresh);

	async function refresh() {
		try {
			status = await getCredentialStatus(provider);
		} catch (err) {
			console.error(err);
		} finally {
			loading = false;
		}
	}

	async function save(e: Event) {
		e.preventDefault();
		const value = newKey.trim();
		if (!value) return;
		saving = true;
		try {
			status = await setCredentialKey(provider, value);
			toast.success(`${title} saved and validated.`);
			newKey = '';
			replacing = false;
			onchange?.();
		} catch (err) {
			toast.error((err as { detail?: string })?.detail ?? 'Could not save the key (it may be invalid).');
		} finally {
			saving = false;
		}
	}

	async function remove() {
		saving = true;
		try {
			status = await deleteCredentialKey(provider);
			toast.success(`${title} removed.`);
			removeOpen = false;
			onchange?.();
		} catch (err) {
			console.error(err);
			toast.error('Could not remove the key.');
		} finally {
			saving = false;
		}
	}

	async function test() {
		testing = true;
		try {
			const result = await testCredentialKey(provider);
			if (result.is_valid) toast.success(result.message);
			else toast.error(result.message);
			await refresh();
			onchange?.();
		} catch (err) {
			console.error(err);
			toast.error('Could not test the key.');
		} finally {
			testing = false;
		}
	}
</script>

<div class="settings-row space-y-3">
	<div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
		<div class="min-w-0 space-y-1">
			<p class="flex flex-wrap items-center gap-2 text-sm font-medium">
				{title}
				{#if !loading}
					{#if status?.configured}
						<StatusBadge status={validation.label} tone={validation.tone} />
					{:else}
						<StatusBadge status="not set" tone="neutral" />
					{/if}
				{/if}
			</p>
			<p class="text-[13px] leading-5 text-muted-foreground">
				{description}
				<a href={signupUrl} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-0.5 settings-link">
					{signupLabel}<ExternalLink class="size-3" aria-hidden="true" />
				</a>
			</p>
			{#if loading}
				<div class="h-4 w-48 animate-pulse rounded bg-muted motion-reduce:animate-none"></div>
			{:else if status?.configured}
				<p class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
					<code class="font-mono text-foreground">{status.key_prefix}…</code>
					<span aria-hidden="true">·</span>
					<span>checked {formatAgo(status.last_validated_at)}</span>
					{#if status.last_validation_message}
						<span aria-hidden="true">·</span><span>{status.last_validation_message}</span>
					{/if}
				</p>
			{:else if status?.global_fallback_available}
				<p class="text-xs text-muted-foreground">
					Until you add one, jobs use the deployment-wide key. Your own keeps usage and limits separate.
				</p>
			{/if}
		</div>
		{#if !loading}
			<div class="flex shrink-0 items-center gap-1.5">
				{#if status?.configured}
					<Button variant="outline" size="sm" onclick={test} disabled={testing || saving} aria-busy={testing}>
						{#if testing}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<RotateCw />{/if}
						Test
					</Button>
					<Button variant="outline" size="sm" onclick={() => (replacing = !replacing)} disabled={saving} aria-expanded={replacing}>
						Replace
					</Button>
					<Button
						variant="ghost"
						size="sm"
						class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
						onclick={() => (removeOpen = true)}
						disabled={saving}
						aria-label="Remove {title}"
						title="Remove"
					>
						<Trash2 />
					</Button>
				{:else if !replacing}
					<Button size="sm" onclick={() => (replacing = true)}><Plus />Add key</Button>
				{/if}
			</div>
		{/if}
	</div>

	{#if replacing}
		<form onsubmit={save} class="space-y-2 rounded-md border border-border bg-muted/30 p-3">
			<Label for={inputId} class="text-[13px]">{status?.configured ? 'New key' : 'Key'}</Label>
			<div class="flex flex-col gap-2 sm:flex-row">
				<!-- svelte-ignore a11y_autofocus -->
				<Input
					id={inputId}
					type="password"
					{placeholder}
					bind:value={newKey}
					disabled={saving}
					autocomplete="off"
					spellcheck={false}
					autofocus
					class="bg-card font-mono"
				/>
				<div class="flex shrink-0 gap-2">
					<Button type="button" variant="ghost" size="sm" class="h-9" onclick={() => ((replacing = false), (newKey = ''))} disabled={saving}>
						Cancel
					</Button>
					<Button type="submit" size="sm" class="h-9" disabled={saving || !newKey.trim()} aria-busy={saving}>
						{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
						Save &amp; validate
					</Button>
				</div>
			</div>
			<p class="text-xs text-muted-foreground">Checked with the provider first, then stored encrypted. It is never shown again.</p>
		</form>
	{/if}
</div>

<ConfirmDialog
	bind:open={removeOpen}
	title="Remove {title}?"
	description="The stored key is deleted. Features that need it fall back to the deployment-wide key where one exists, or stop until you add a new key."
	confirmLabel="Remove"
	destructive
	busy={saving}
	onConfirm={remove}
/>
