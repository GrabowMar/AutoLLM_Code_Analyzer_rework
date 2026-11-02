<script lang="ts">
	/**
	 * SkipDialog — skip the current app with a reason. "The app itself is
	 * broken" (app_broken: it fails to build or start, crashes…) and "The
	 * platform could not show the app" (platform_unavailable: queue, hosting
	 * platform, portal) are kept apart, because only app_broken makes an app
	 * unevaluable (R1). The apps' current hosting state is shown, and picks the
	 * starting reason (defaultSkipReason).
	 *
	 * Props: `open` (bindable), `apps`, `hosting` (per slot), `busy`,
	 * `onconfirm(reason, note)`.
	 */
	import { untrack } from 'svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { HostingInfo, PortalApp, SkipReason } from '$lib/api/types/human-eval';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Textarea } from '$lib/components/ui/textarea';
	import { RadioGroup } from 'bits-ui';
	import { cn } from '$lib/utils';
	import { HOSTING_LABELS } from './hosting';
	import { SKIP_REASONS, defaultSkipReason, slotTitle } from './workbench';

	interface Props {
		open: boolean;
		apps: PortalApp[];
		hosting: Partial<Record<string, HostingInfo>>;
		busy?: boolean;
		onconfirm: (reason: SkipReason, note: string) => void;
	}

	let { open = $bindable(false), apps, hosting, busy = false, onconfirm }: Props = $props();
	const uid = $props.id();

	let reason = $state<string>('');
	let note = $state('');
	let tried = $state(false);

	const suggested = $derived(defaultSkipReason(apps.map((a) => a.slot), hosting));
	const noteRequired = $derived(reason === 'other');
	const canConfirm = $derived(!!reason && (!noteRequired || note.trim().length > 0));

	$effect(() => {
		if (!open) return;
		untrack(() => {
			reason = suggested;
			note = '';
			tried = false;
		});
	});

	function confirm(event: SubmitEvent) {
		event.preventDefault();
		tried = true;
		if (!canConfirm || busy) return;
		onconfirm(reason as SkipReason, note.trim());
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[92dvh] max-w-lg overflow-y-auto">
		<form class="space-y-4" onsubmit={confirm}>
			<Dialog.Header>
				<Dialog.Title>Skip {apps.length > 1 ? 'this pair' : 'this app'}?</Dialog.Title>
				<Dialog.Description>
					Your answers so far are kept but not counted. Tell us why, so broken apps and platform problems can be told apart.
				</Dialog.Description>
			</Dialog.Header>

			<ul class="space-y-1 rounded-md border border-border bg-surface-2 px-3 py-2 text-xs">
				{#each apps as app (app.slot)}
					{@const h = hosting[app.slot]}
					<li class="flex flex-wrap items-baseline gap-x-2">
						<span class="font-medium">{slotTitle(app)}</span>
						<span class="text-muted-foreground">{h ? HOSTING_LABELS[h.state] : 'Unknown'}{h?.error ? ` — ${h.error}` : ''}</span>
					</li>
				{/each}
			</ul>

			<RadioGroup.Root bind:value={reason} aria-labelledby="{uid}-reasons" class="space-y-1.5">
				<p id="{uid}-reasons" class="text-sm font-medium">Reason</p>
				{#each SKIP_REASONS as option (option.value)}
					<RadioGroup.Item
						value={option.value}
						class={cn(
							'flex w-full cursor-pointer items-start gap-2.5 rounded-md border border-border px-3 py-2 text-left transition-colors duration-150 hover:border-primary/50',
							'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 data-[state=checked]:border-primary data-[state=checked]:bg-primary/5'
						)}
					>
						{#snippet children({ checked })}
							<span
								class={cn(
									'mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border',
									checked ? 'border-primary' : 'border-muted-foreground/60'
								)}
								aria-hidden="true"
							>
								{#if checked}<span class="size-2 rounded-full bg-primary"></span>{/if}
							</span>
							<span class="min-w-0">
								<span class="block text-sm font-medium">{option.label}</span>
								<span class="block text-xs text-muted-foreground">{option.description}</span>
							</span>
						{/snippet}
					</RadioGroup.Item>
				{/each}
			</RadioGroup.Root>
			{#if tried && !reason}<p class="text-[11px] font-medium text-danger-fg">Pick a reason.</p>{/if}

			<div class="space-y-1.5">
				<label for="{uid}-note" class="text-sm font-medium">
					What happened {noteRequired ? '' : '(optional)'}
				</label>
				<Textarea
					id="{uid}-note"
					rows={3}
					bind:value={note}
					maxlength={2000}
					aria-required={noteRequired}
					aria-invalid={tried && noteRequired && !note.trim()}
					placeholder="e.g. the login page shows a server error"
				/>
				{#if tried && noteRequired && !note.trim()}<p class="text-[11px] font-medium text-danger-fg">Say briefly what happened.</p>{/if}
			</div>

			<Dialog.Footer>
				<Button type="button" variant="outline" size="sm" disabled={busy} onclick={() => (open = false)}>Keep working</Button>
				<Button type="submit" size="sm" disabled={busy} aria-busy={busy}>
					{#if busy}<LoaderCircle class="animate-spin" />{/if}
					Skip
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
