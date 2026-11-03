<script lang="ts">
	/**
	 * SlotTargetPicker — the units of a slot study (spec 10.2 step 2): targets grouped by their parent
	 * (SlotSet), with the parent's model and harness, the target's validation and leak status, and
	 * "Add matching part from other parents" (`POST /slots/targets/{id}/match/`, `same_task`). Only usable
	 * targets (validated, discriminating, no verbatim leak) can be chosen; the others show why.
	 *
	 * Props: `slotSets`, `targets` (uuids), `onchange({slot_sets, slot_targets})`, `disabled`.
	 */
	import { untrack } from 'svelte';
	import { getSlotSet, listSlotSets, matchSlotTarget } from '$lib/api/v3/slots';
	import type { SlotSetDetailOut, SlotTargetOut } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { cn } from '$lib/utils';
	import { formatPct, refLabel } from '../format';
	import Plus from '@lucide/svelte/icons/plus';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		slotSets: string[];
		targets: string[];
		onchange: (units: { slot_sets: string[]; slot_targets: string[] }) => void;
		disabled?: boolean;
	}

	let { slotSets, targets, onchange, disabled = false }: Props = $props();

	let sets = $state<SlotSetDetailOut[]>([]);
	let loading = $state(false);
	let error = $state('');
	let matching = $state<string | null>(null);
	let note = $state('');
	let others = $state<{ id: string; name: string; parent_model: string }[]>([]);

	const chosen = $derived(new Set(targets));
	const task = $derived(sets.find((s) => s.task)?.task ?? null);

	async function load(ids: string[]) {
		loading = true;
		error = '';
		try {
			sets = await Promise.all(ids.map((id) => getSlotSet(id)));
			const taskRef = sets.find((s) => s.task)?.task;
			if (taskRef) {
				const page = await listSlotSets({ task: taskRef, page_size: 100 });
				others = page.items.filter((s) => !ids.includes(s.id)).map((s) => ({ id: s.id, name: s.name, parent_model: s.parent_model }));
			}
		} catch (e) {
			error = formatApiError(e, 'Could not load the slot sets.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const ids = [...slotSets];
		untrack(() => {
			const known = new Set(sets.map((s) => s.id));
			if (ids.length === 0) sets = [];
			else if (ids.some((id) => !known.has(id)) || ids.length !== sets.length) void load(ids);
		});
	});

	function emit(nextTargets: string[], nextSets = slotSets) {
		onchange({ slot_sets: [...new Set(nextSets)], slot_targets: [...new Set(nextTargets)] });
	}

	function toggle(target: SlotTargetOut, on: boolean) {
		emit(on ? [...targets, target.id] : targets.filter((t) => t !== target.id));
	}

	async function addMatching(target: SlotTargetOut) {
		matching = target.id;
		note = '';
		try {
			const out = await matchSlotTarget(target.id, 'same_task');
			const created = out.created.filter((t) => t.usable);
			emit([...targets, ...created.map((t) => t.id)], [...slotSets, ...created.map((t) => t.slot_set)]);
			note =
				`${created.length} matching part${created.length === 1 ? '' : 's'} added` +
				(out.unmatched.length ? `; ${out.unmatched.length} parent${out.unmatched.length === 1 ? '' : 's'} had none (${out.unmatched.map((u) => u.reason).join(', ')})` : '');
		} catch (e) {
			note = formatApiError(e, 'Could not match the part in other parents.');
		} finally {
			matching = null;
		}
	}

	function addSet(id: string) {
		emit(targets, [...slotSets, id]);
	}

	const validationTone = (status: string) => (status === 'ok' ? 'success' : status === 'pending' ? 'info' : 'warning');
</script>

<div class="space-y-3">
	{#if slotSets.length === 0}
		<EmptyState
			compact
			title="No parents yet"
			description="Freeze one or more completed agentic runs as parents and pick the parts to regenerate."
		>
			{#snippet action()}<Button href="/slots/new" size="sm" variant="outline">Open the slot picker</Button>{/snippet}
		</EmptyState>
	{:else if error}
		<ErrorAlert message={error} onretry={() => load(slotSets)} />
	{:else if loading && sets.length === 0}
		<LoadingState label="loading slot sets" />
	{:else}
		{#if task}<p class="text-xs text-muted-foreground">Task <span class="font-mono">{refLabel(task)}</span> (fixed by the parents)</p>{/if}
		{#each sets as set (set.id)}
			<div class="rounded-md border border-border">
				<div class="flex flex-wrap items-center gap-2 border-b border-border bg-muted/30 px-3 py-2 text-xs">
					<a href="/slots/{set.id}" class="font-medium text-foreground hover:underline">{set.name || set.id.slice(0, 8)}</a>
					<span class="text-muted-foreground">parent {set.parent_model} · {set.parent_harness}</span>
					{#if set.parent_eval}
						<span class="text-muted-foreground">pass rate {formatPct(set.parent_eval.agent_pass_rate)}</span>
					{/if}
					<span class="ml-auto text-muted-foreground">drawn: {set.selection?.rule ?? 'manual'}</span>
				</div>
				{#if set.targets.length === 0}
					<p class="px-3 py-2 text-xs text-muted-foreground">No targets yet. <a class="underline" href="/slots/{set.id}">Pick parts</a>.</p>
				{:else}
					<ul class="divide-y divide-border">
						{#each set.targets as target (target.id)}
							<li class={cn('flex flex-wrap items-center gap-2 px-3 py-2 text-xs', chosen.has(target.id) && 'bg-primary/[0.04]')}>
								<Checkbox
									checked={chosen.has(target.id)}
									disabled={disabled || !target.usable}
									aria-label="Use {target.label || target.address.path}"
									onCheckedChange={(v) => toggle(target, v === true)}
								/>
								<span class="font-medium">{target.label || target.kind}</span>
								<span class="truncate font-mono text-muted-foreground" title={target.address.path}>{target.address.path}</span>
								<StatusBadge status={target.validation.status} tone={validationTone(target.validation.status)} label="validation {target.validation.status}" />
								{#if target.leak.matches.some((m) => m.verbatim)}
									<StatusBadge status="leak" tone="danger" label="verbatim leak" />
								{:else}
									<span class="text-muted-foreground" title="Leak score (shingle overlap with the context)">leak {target.leak.score.toFixed(2)}</span>
								{/if}
								{#if !target.usable && target.unusable_reason}
									<span class="text-warning-fg">{target.unusable_reason}</span>
								{/if}
								{#if !disabled}
									<button
										type="button"
										class="ml-auto inline-flex cursor-pointer items-center gap-1 text-primary hover:underline disabled:opacity-50"
										disabled={matching !== null}
										onclick={() => addMatching(target)}
									>
										{#if matching === target.id}<LoaderCircle class="size-3 animate-spin" />{:else}<Plus class="size-3" />{/if}
										Add matching part from other parents
									</button>
								{/if}
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/each}
		{#if note}<p class="text-xs text-muted-foreground" role="status">{note}</p>{/if}
		{#if others.length > 0 && !disabled}
			<div class="space-y-1">
				<p class="section-label">Other parents of this task</p>
				<div class="flex flex-wrap gap-1.5">
					{#each others as other (other.id)}
						<Button variant="outline" size="sm" onclick={() => addSet(other.id)}>
							<Plus />{other.name || other.id.slice(0, 8)} · {other.parent_model}
						</Button>
					{/each}
				</div>
			</div>
		{/if}
	{/if}
</div>
