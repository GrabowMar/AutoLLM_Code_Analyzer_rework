<script lang="ts">
	/**
	 * WebhooksSection — outbound notifications (Slack, Discord, Teams, ntfy,
	 * Telegram, signed generic JSON): list, add/edit, enable, test, deliveries.
	 */
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import SettingsBlock from '$lib/components/users-settings/SettingsBlock.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatApiError } from '$lib/api/core';
	import { formatAgo } from '$lib/components/inference/endpoint-display';
	import {
		deleteWebhook,
		listWebhookEventTypes,
		listWebhooks,
		testWebhook,
		updateWebhook,
		type WebhookEventType,
		type WebhookSubscription,
	} from '$lib/api/integrations';
	import WebhookFormDialog from './WebhookFormDialog.svelte';
	import WebhookDeliveries from './WebhookDeliveries.svelte';
	import { webhookKind } from './webhook-kinds';
	import { useSectionStatus } from '$lib/components/users-settings/section-status.svelte';
	import Bell from '@lucide/svelte/icons/bell';
	import Plus from '@lucide/svelte/icons/plus';
	import Send from '@lucide/svelte/icons/send';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import History from '@lucide/svelte/icons/history';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	const MAX_WEBHOOKS = 25;

	const report = useSectionStatus('notifications');
	$effect(() => {
		if (loading || error) return;
		const on = subs.filter((s) => s.enabled).length;
		report(on ? { tone: 'success', label: `${on} on` } : null);
	});

	let subs = $state<WebhookSubscription[]>([]);
	let eventTypes = $state<WebhookEventType[]>([]);
	let loading = $state(true);
	let error = $state('');

	let formOpen = $state(false);
	let editing = $state<WebhookSubscription | null>(null);
	let deleting = $state<WebhookSubscription | null>(null);
	let deleteOpen = $state(false);
	let deleteBusy = $state(false);
	let testingId = $state<number | null>(null);
	let expandedId = $state<number | null>(null);
	let deliveriesKey = $state(0);

	async function load() {
		loading = subs.length === 0;
		error = '';
		try {
			const [s, e] = await Promise.all([listWebhooks(), eventTypes.length ? eventTypes : listWebhookEventTypes()]);
			subs = s;
			eventTypes = e;
		} catch (err) {
			error = formatApiError(err, 'Could not load notifications.');
		} finally {
			loading = false;
		}
	}

	function replace(sub: WebhookSubscription) {
		const i = subs.findIndex((s) => s.id === sub.id);
		subs = i === -1 ? [sub, ...subs] : subs.map((s) => (s.id === sub.id ? sub : s));
	}

	function openCreate() {
		editing = null;
		formOpen = true;
	}

	function openEdit(sub: WebhookSubscription) {
		editing = sub;
		formOpen = true;
	}

	function saved(sub: WebhookSubscription) {
		toast.success(editing ? `Saved ${sub.name}.` : `Added ${sub.name}. Send a test to check it.`);
		replace(sub);
	}

	async function setEnabled(sub: WebhookSubscription, enabled: boolean) {
		try {
			replace(await updateWebhook(sub.id, { enabled }));
		} catch (e) {
			toast.error(formatApiError(e, 'Could not update the webhook.'));
			replace({ ...sub });
		}
	}

	async function sendTest(sub: WebhookSubscription) {
		testingId = sub.id;
		try {
			const res = await testWebhook(sub.id);
			if (res.ok) toast.success(`Test sent to ${sub.name}${res.status_code ? ` (HTTP ${res.status_code})` : ''}.`);
			else toast.error(`Test failed: ${res.error || `HTTP ${res.status_code ?? '?'}`}`);
			deliveriesKey += 1;
			// Refresh last_success / last_error.
			void load();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not send the test.'));
		} finally {
			testingId = null;
		}
	}

	function askDelete(sub: WebhookSubscription) {
		deleting = sub;
		deleteOpen = true;
	}

	async function confirmDelete() {
		if (!deleting) return;
		deleteBusy = true;
		try {
			await deleteWebhook(deleting.id);
			subs = subs.filter((s) => s.id !== deleting?.id);
			if (expandedId === deleting.id) expandedId = null;
			deleteOpen = false;
		} catch (e) {
			toast.error(formatApiError(e, 'Could not delete the webhook.'));
		} finally {
			deleteBusy = false;
		}
	}

	onMount(load);
</script>

<SettingsBlock title="Channels" description="Targets and secrets are stored encrypted and never shown again.">
	{#snippet actions()}
		<Button size="sm" onclick={openCreate} disabled={loading || subs.length >= MAX_WEBHOOKS}>
			<Plus />Add channel
		</Button>
	{/snippet}

	{#if error}
		<div class="settings-row"><ErrorAlert message={error} onretry={load} /></div>
	{:else if loading}
		<LoadingState label="loading notifications" />
	{:else if subs.length === 0}
		<EmptyState
			compact
			icon={Bell}
			title="No channels yet"
			description="Add Slack, Discord, Teams, Telegram, ntfy or a webhook to hear about finished runs."
		/>
	{:else}
		{#each subs as sub (sub.id)}
			{@const kindInfo = webhookKind(sub.kind)}
			<div>
				<div class="settings-row flex items-start gap-3.5">
					<Switch
						checked={sub.enabled}
						label="{sub.enabled ? 'Disable' : 'Enable'} {sub.name}"
						onCheckedChange={(v) => setEnabled(sub, v)}
						class="mt-0.5"
					/>
					<div class="min-w-0 flex-1 space-y-1">
						<p class="flex flex-wrap items-center gap-2 text-sm">
							<span class="font-medium">{sub.name}</span>
							<span class="text-xs text-muted-foreground">{kindInfo?.label ?? sub.kind}</span>
							{#if !sub.enabled}
								<StatusBadge status="disabled" tone="neutral" />
							{:else if sub.last_error && (!sub.last_success_at || (sub.last_error_at ?? '') > sub.last_success_at)}
								<StatusBadge status="failing" tone="danger" />
							{:else if sub.last_success_at}
								<StatusBadge status="ok" tone="success" label="delivering" />
							{/if}
						</p>
						<p class="flex flex-wrap gap-x-2 gap-y-0.5 text-xs text-muted-foreground">
							{#if sub.kind === 'telegram'}
								<span>chat <span class="font-mono">{sub.telegram_chat_id}</span></span>
								<span aria-hidden="true">·</span>
							{:else if sub.url_hint}
								<span class="font-mono">{sub.url_hint}</span>
								<span aria-hidden="true">·</span>
							{/if}
							{#if sub.has_secret}<span>secret <span class="font-mono">{sub.secret_hint}</span></span><span aria-hidden="true">·</span>{/if}
							<span>last success {formatAgo(sub.last_success_at)}</span>
						</p>
						{#if sub.last_error}
							<p class="truncate text-xs text-danger-fg" title={sub.last_error}>
								{formatAgo(sub.last_error_at)}: {sub.last_error}
							</p>
						{/if}
						<p class="flex flex-wrap gap-1 pt-0.5">
							{#if sub.events.length === 0}
								<span class="text-xs text-warning-fg">No events selected: only tests are sent.</span>
							{:else}
								{#each sub.events as ev (ev)}<code class="mono-tag">{ev}</code>{/each}
							{/if}
							{#if sub.include_experiment_jobs}<code class="mono-tag">+ experiment jobs</code>{/if}
						</p>
					</div>
					<div class="flex shrink-0 items-center gap-0.5">
						<Button variant="ghost" size="sm" onclick={() => sendTest(sub)} disabled={testingId === sub.id}>
							{#if testingId === sub.id}<LoaderCircle class="animate-spin" />{:else}<Send />{/if}Test
						</Button>
						<Button
							variant="ghost"
							size="sm"
							class="size-8 p-0"
							aria-expanded={expandedId === sub.id}
							aria-label="Recent deliveries of {sub.name}"
							title="Recent deliveries"
							onclick={() => (expandedId = expandedId === sub.id ? null : sub.id)}
						>
							<History />
						</Button>
						<Button variant="ghost" size="sm" class="size-8 p-0" aria-label="Edit {sub.name}" title="Edit" onclick={() => openEdit(sub)}>
							<Pencil />
						</Button>
						<Button
							variant="ghost"
							size="sm"
							class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
							aria-label="Delete {sub.name}"
							title="Delete"
							onclick={() => askDelete(sub)}
						>
							<Trash2 />
						</Button>
					</div>
				</div>
				{#if expandedId === sub.id}
					<div class="border-t border-border bg-muted/20 px-4 py-3">
						<p class="mb-2 text-xs font-medium text-muted-foreground">Recent deliveries</p>
						<WebhookDeliveries subscriptionId={sub.id} refreshKey={deliveriesKey} />
					</div>
				{/if}
			</div>
		{/each}
	{/if}
</SettingsBlock>
{#if subs.length >= MAX_WEBHOOKS}
	<p class="settings-row text-xs text-muted-foreground">At most {MAX_WEBHOOKS} channels per account.</p>
{/if}

<WebhookFormDialog bind:open={formOpen} subscription={editing} {eventTypes} onsaved={saved} />

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete {deleting?.name ?? 'this notification'}?"
	description="Removes the webhook and its delivery history. Messages already sent are not affected."
	confirmLabel="Delete"
	destructive
	busy={deleteBusy}
	onConfirm={confirmDelete}
/>
