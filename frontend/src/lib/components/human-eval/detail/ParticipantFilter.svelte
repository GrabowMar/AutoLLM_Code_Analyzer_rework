<script lang="ts">
	/**
	 * ParticipantFilter — pick one participant (or any) to filter the Responses
	 * views. A combobox searched on the server (`listParticipants` `search`:
	 * label "P12" or number, email, note, invite label, access-code hint), so
	 * tasks with hundreds of participants stay usable; the first
	 * PARTICIPANT_SEARCH_LIMIT by number show before anything is typed.
	 *
	 *   <ParticipantFilter taskId={task.id} bind:value={participantId} fallbackLabel={label} onValueChange={resetPages} />
	 *
	 * Props:
	 * - `taskId`.
	 * - `value` (bindable): participant id; '' = any participant.
	 * - `fallbackLabel`: label for a `value` set from outside (deep link) before
	 *   any search returned it, e.g. from the filtered rows.
	 * - `onValueChange(id)`: after the researcher picks (or clears).
	 * - `class`: width of the field (default w-40).
	 */
	import { onDestroy, untrack } from 'svelte';
	import { SvelteMap } from 'svelte/reactivity';
	import { Combobox } from 'bits-ui';
	import * as Select from '$lib/components/ui/select';
	import { isAbortError } from '$lib/api/core';
	import { listParticipants } from '$lib/api/human-eval';
	import { cn } from '$lib/utils';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import X from '@lucide/svelte/icons/x';
	import {
		PARTICIPANT_SEARCH_LIMIT,
		participantMatchesNote,
		participantOption,
		selectedParticipantLabel,
		type ParticipantOption,
	} from './participant-filter';

	interface Props {
		taskId: string;
		value?: string;
		fallbackLabel?: string;
		onValueChange?: (value: string) => void;
		class?: string;
	}

	let { taskId, value = $bindable(''), fallbackLabel = '', onValueChange, class: className }: Props = $props();

	// bits-ui treats '' as "no value", so "Any participant" needs its own token.
	const ANY = '__any_participant__';

	let open = $state(false);
	let search = $state('');
	let matches = $state.raw<ParticipantOption[]>([]);
	let total = $state(0);
	let loading = $state(false);
	let failed = $state(false);
	const known = new SvelteMap<string, string>();

	const selectedLabel = $derived(selectedParticipantLabel(value, known, fallbackLabel));
	const shown = $derived(open ? search : selectedLabel);
	const note = $derived(participantMatchesNote(matches.length, total, search));
	const comboItems = $derived([{ value: ANY, label: 'Any participant' }, ...matches.map((m) => ({ value: m.value, label: m.label }))]);

	let ctl: AbortController | null = null;
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function runSearch(term: string) {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		loading = true;
		failed = false;
		try {
			const res = await listParticipants(taskId, { search: term.trim() || undefined, per_page: PARTICIPANT_SEARCH_LIMIT }, { signal: mine.signal });
			matches = res.items.map(participantOption);
			total = res.total;
			for (const m of matches) known.set(m.value, m.label);
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			failed = true;
			matches = [];
			total = 0;
		} finally {
			if (ctl === mine) loading = false;
		}
	}

	function schedule(term: string, delay: number) {
		clearTimeout(timer);
		timer = setTimeout(() => void runSearch(term), delay);
	}

	// Opening (trigger, click, typing) searches what is typed so far; closing drops the
	// search so the field shows the selected participant again.
	let wasOpen = false;
	$effect(() => {
		const isOpen = open;
		untrack(() => {
			if (isOpen === wasOpen) return;
			wasOpen = isOpen;
			if (isOpen) {
				schedule(search, 0);
			} else {
				search = '';
				clearTimeout(timer);
				ctl?.abort();
			}
		});
	});

	function onInput(e: Event) {
		search = (e.currentTarget as HTMLInputElement).value;
		if (!open) open = true;
		schedule(search, 200);
	}

	function pick(next: string) {
		const id = next === ANY ? '' : next;
		if (id === value) return;
		value = id;
		onValueChange?.(id);
	}

	function clear() {
		pick('');
	}

	onDestroy(() => {
		clearTimeout(timer);
		ctl?.abort();
	});
</script>

<Combobox.Root type="single" value={value || ''} onValueChange={pick} bind:open items={comboItems} allowDeselect={false}>
	<div class={cn('relative w-40', className)}>
		<Combobox.Input>
			{#snippet child({ props })}
				<input
					{...props}
					value={shown}
					oninput={(e) => {
						(props.oninput as ((ev: Event) => void) | undefined)?.(e);
						onInput(e);
					}}
					onclick={() => {
						if (!open) open = true;
					}}
					onfocus={(e) => e.currentTarget.select()}
					aria-label="Participant"
					placeholder={open ? 'Search P12, email…' : 'Any participant'}
					autocomplete="off"
					spellcheck="false"
					class={cn(
						'flex h-8 w-full min-w-0 rounded-md border border-input bg-surface-1 py-1 pl-3 text-xs shadow-xs transition-colors duration-150',
						'placeholder:text-muted-foreground hover:border-primary/40 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30',
						value ? 'pr-12' : 'pr-7'
					)}
				/>
			{/snippet}
		</Combobox.Input>
		{#if value}
			<button
				type="button"
				class="absolute right-6 top-1/2 inline-flex size-5 -translate-y-1/2 cursor-pointer items-center justify-center rounded text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
				aria-label="Any participant (clear the filter)"
				title="Any participant"
				onclick={clear}
			>
				<X class="size-3.5" aria-hidden="true" />
			</button>
		{/if}
		<Combobox.Trigger
			class="absolute right-1 top-1/2 inline-flex size-5 -translate-y-1/2 cursor-pointer items-center justify-center rounded text-muted-foreground hover:text-foreground"
			aria-label="Show participants"
		>
			<ChevronDown class="size-4" aria-hidden="true" />
		</Combobox.Trigger>
	</div>
	<Combobox.Portal>
		<Combobox.Content
			sideOffset={4}
			align="start"
			class={cn(
				'relative z-50 max-h-[min(22rem,var(--bits-combobox-content-available-height))] w-72 min-w-(--bits-combobox-anchor-width) overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-md',
				'data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0'
			)}
		>
			<Combobox.Viewport class="max-h-[min(20rem,calc(var(--bits-combobox-content-available-height)-2rem))] w-full overflow-y-auto p-1">
				<Select.Item value={ANY} label="Any participant">
					<span class="text-muted-foreground">Any participant</span>
				</Select.Item>
				<Select.Separator />
				{#each matches as m (m.value)}
					<Select.Item value={m.value} label={m.label}>
						<span class="flex min-w-0 flex-col">
							<span class="truncate font-mono">{m.label}</span>
							{#if m.description}<span class="truncate text-[11px] text-muted-foreground">{m.description}</span>{/if}
						</span>
					</Select.Item>
				{/each}
			</Combobox.Viewport>
			<p class="border-t border-border px-2 py-1.5 text-[11px] text-muted-foreground" role="status" aria-live="polite">
				{#if loading}
					Searching…
				{:else if failed}
					Could not load participants.
				{:else if matches.length === 0}
					{search.trim() ? `No participant matches “${search.trim()}”.` : 'No participants yet.'}
				{:else if note}
					{note}
				{:else}
					{matches.length === 1 ? '1 participant' : `${matches.length} participants`}
				{/if}
			</p>
		</Combobox.Content>
	</Combobox.Portal>
</Combobox.Root>
