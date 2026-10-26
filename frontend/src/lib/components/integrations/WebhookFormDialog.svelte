<script lang="ts">
	/**
	 * WebhookFormDialog — create a notification webhook, or edit one.
	 *
	 * Props: `open` (bindable), `subscription` (null = create), `eventTypes`,
	 * `onsaved(sub)`. URLs and secrets are write-only: when editing, an empty
	 * field keeps the stored value. The kind cannot change after creation.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		createWebhook,
		updateWebhook,
		type WebhookEventType,
		type WebhookKind,
		type WebhookPatchPayload,
		type WebhookSubscription,
	} from '$lib/api/integrations';
	import { WEBHOOK_KINDS, webhookKind } from './webhook-kinds';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		open?: boolean;
		subscription: WebhookSubscription | null;
		eventTypes: WebhookEventType[];
		onsaved?: (sub: WebhookSubscription) => void;
	}

	let { open = $bindable(false), subscription, eventTypes, onsaved }: Props = $props();

	let name = $state('');
	let kind = $state<string>('slack');
	let url = $state('');
	let secret = $state('');
	let clearSecret = $state(false);
	let chatId = $state('');
	let events = $state<string[]>([]);
	let includeExperimentJobs = $state(false);
	let saving = $state(false);
	let error = $state('');

	const editing = $derived(subscription !== null);
	const info = $derived(webhookKind(kind) ?? WEBHOOK_KINDS[0]);
	const kindItems = WEBHOOK_KINDS.map((k) => ({ value: k.value, label: k.label }));
	const needsSecret = $derived(!editing && !!info.secret?.required);
	const canSave = $derived(
		!saving &&
			name.trim().length > 0 &&
			(!info.usesUrl || editing || url.trim().length > 0) &&
			(info.usesUrl || editing || chatId.trim().length > 0) &&
			(!needsSecret || secret.trim().length > 0),
	);

	// Reset the form from the subscription on every open.
	$effect(() => {
		if (!open) return;
		const s = subscription;
		name = s?.name ?? '';
		kind = s?.kind ?? 'slack';
		url = '';
		secret = '';
		clearSecret = false;
		chatId = s?.telegram_chat_id ?? '';
		events = s ? [...s.events] : eventTypes.map((e) => e.id).filter((id) => !id.endsWith('.cancelled'));
		includeExperimentJobs = s?.include_experiment_jobs ?? false;
		error = '';
	});

	function toggleEvent(id: string, on: boolean) {
		events = on ? [...events.filter((e) => e !== id), id] : events.filter((e) => e !== id);
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!canSave) return;
		saving = true;
		error = '';
		try {
			let saved: WebhookSubscription;
			if (subscription) {
				const patch: WebhookPatchPayload = {
					name: name.trim(),
					events,
					include_experiment_jobs: includeExperimentJobs,
				};
				if (info.usesUrl && url.trim()) patch.url = url.trim();
				if (!info.usesUrl && chatId.trim() && chatId.trim() !== subscription.telegram_chat_id)
					patch.telegram_chat_id = chatId.trim();
				if (secret.trim()) patch.secret = secret.trim();
				else if (clearSecret) patch.secret = '';
				saved = await updateWebhook(subscription.id, patch);
			} else {
				saved = await createWebhook({
					name: name.trim(),
					kind: kind as WebhookKind,
					...(info.usesUrl ? { url: url.trim() } : { telegram_chat_id: chatId.trim() }),
					...(secret.trim() ? { secret: secret.trim() } : {}),
					events,
					include_experiment_jobs: includeExperimentJobs,
				});
			}
			open = false;
			onsaved?.(saved);
		} catch (e) {
			error = formatApiError(e, 'Could not save the webhook.');
		} finally {
			saving = false;
		}
	}
</script>

<Dialog.Root
	{open}
	onOpenChange={(next) => {
		if (!saving || next) open = next;
	}}
>
	<Dialog.Content class="max-h-[90vh] max-w-lg overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>{editing ? 'Edit notification' : 'Add notification'}</Dialog.Title>
			<Dialog.Description>
				{editing
					? 'Leave the URL and secret empty to keep the stored ones.'
					: 'Post a message when runs, analyses, experiments or reports finish.'}
			</Dialog.Description>
		</Dialog.Header>
		<form class="mt-4 space-y-4" onsubmit={submit}>
			<div class="grid gap-3 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="wh-kind">Channel</Label>
					<Select.Simple id="wh-kind" bind:value={kind} items={kindItems} disabled={editing} />
				</div>
				<div class="space-y-1.5">
					<Label for="wh-name">Name</Label>
					<Input id="wh-name" bind:value={name} maxlength={120} placeholder="#llm-lab alerts" />
				</div>
			</div>

			{#if info.usesUrl}
				<div class="space-y-1.5">
					<Label for="wh-url">Webhook URL</Label>
					<Input
						id="wh-url"
						type="password"
						bind:value={url}
						autocomplete="off"
						spellcheck={false}
						placeholder={editing && subscription?.url_hint ? subscription.url_hint : info.urlPlaceholder}
						aria-describedby="wh-url-help"
					/>
					<p id="wh-url-help" class="text-xs text-muted-foreground">
						{info.urlHelp} Stored encrypted; only a masked hint is shown later.
					</p>
				</div>
			{:else}
				<div class="space-y-1.5">
					<Label for="wh-chat">Chat id</Label>
					<Input id="wh-chat" bind:value={chatId} maxlength={64} placeholder="123456789, -100… or @channel" />
				</div>
			{/if}

			{#if info.secret}
				<div class="space-y-1.5">
					<Label for="wh-secret">
						{info.secret.label}
						{#if !info.secret.required}<span class="text-muted-foreground">(optional)</span>{/if}
					</Label>
					<Input
						id="wh-secret"
						type="password"
						bind:value={secret}
						autocomplete="off"
						spellcheck={false}
						disabled={clearSecret}
						placeholder={editing && subscription?.has_secret ? `stored ${subscription.secret_hint}` : ''}
						aria-describedby="wh-secret-help"
					/>
					<p id="wh-secret-help" class="text-xs text-muted-foreground">{info.secret.help}</p>
					{#if editing && subscription?.has_secret && !info.secret.required}
						<label class="flex cursor-pointer items-center gap-2 text-sm">
							<Checkbox bind:checked={clearSecret} />
							Remove the stored {info.secret.label.toLowerCase()}
						</label>
					{/if}
				</div>
			{/if}

			<fieldset class="space-y-2">
				<legend class="section-label mb-1">Events</legend>
				{#each eventTypes as et (et.id)}
					<label class="flex cursor-pointer items-start gap-2 text-sm">
						<Checkbox
							class="mt-0.5"
							checked={events.includes(et.id)}
							onCheckedChange={(v) => toggleEvent(et.id, v === true)}
						/>
						<span>
							<code class="font-mono text-xs">{et.id}</code>
							<span class="block text-xs text-muted-foreground">{et.description}</span>
						</span>
					</label>
				{/each}
				<label class="flex cursor-pointer items-start gap-2 border-t border-border pt-2 text-sm">
					<Checkbox class="mt-0.5" bind:checked={includeExperimentJobs} />
					<span>
						Include experiment jobs
						<span class="block text-xs text-muted-foreground">
							Also send generation events for each job of an experiment (can be many messages).
						</span>
					</span>
				</label>
			</fieldset>

			{#if error}<ErrorAlert message={error} />{/if}

			<Dialog.Footer class="gap-2">
				<Button type="button" variant="outline" size="sm" onclick={() => (open = false)} disabled={saving}>Cancel</Button>
				<Button type="submit" size="sm" disabled={!canSave} aria-busy={saving}>
					{#if saving}<LoaderCircle class="animate-spin" />{/if}{editing ? 'Save' : 'Add'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
