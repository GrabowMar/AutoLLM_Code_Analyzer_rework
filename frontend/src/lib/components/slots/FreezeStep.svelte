<script lang="ts">
	/**
	 * FreezeStep — step 1 of the slot picker (spec 7.2, 10.5): summary cards of the chosen parents, then
	 * `POST /slots/sets/` per parent. Freezing copies nothing (blobs are immutable; PROTECT pins the parent),
	 * builds the symbol index when missing and records how the parent was chosen. A parent already frozen can
	 * reuse its newest SlotSet. Build/start-failed parents need "allow unbuilt parent" (no runtime slot tests,
	 * never confirmatory).
	 *
	 * Props: `parents` (run ids), `selection` (how they were chosen), `poolSize`, `onfrozen(setIds)`, `onback`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Snowflake from '@lucide/svelte/icons/snowflake';
	import { formatApiError } from '$lib/api/core';
	import { getSample } from '$lib/api/v3/samples';
	import { createSlotSet, listSlotSets } from '$lib/api/v3/slots';
	import type { ParentSelection, RunOut, SlotSetOut } from '$lib/api/v3/types';
	import { fmtRate, fmtUsd, OUTCOME_TONES, outcomeLabel, shortSha } from '$lib/components/runs/format';

	interface Props {
		parents: string[];
		selection: ParentSelection | null;
		poolSize?: number | null;
		onfrozen: (setIds: string[]) => void;
		onback?: () => void;
	}

	let { parents, selection, poolSize = null, onfrozen, onback }: Props = $props();

	interface Entry {
		runId: string;
		run: RunOut | null;
		existing: SlotSetOut[];
		reuse: boolean;
		name: string;
		allowUnbuilt: boolean;
		error: string;
	}

	let entries = $state<Entry[]>([]);
	let loading = $state(true);
	let freezing = $state(false);

	$effect(() => {
		const ids = parents;
		loading = true;
		Promise.all(
			ids.map(async (runId): Promise<Entry> => {
				try {
					const [run, sets] = await Promise.all([getSample(runId), listSlotSets({ parent_run: runId, page_size: 20 })]);
					const name = `${run.labels?.task ?? 'parent'} · ${run.model ?? ''} · ${shortSha(runId, 8)}`;
					return { runId, run, existing: sets.items, reuse: sets.items.length > 0, name, allowUnbuilt: false, error: '' };
				} catch (e) {
					return { runId, run: null, existing: [], reuse: false, name: '', allowUnbuilt: false, error: formatApiError(e, 'Could not load this parent.') };
				}
			}),
		)
			.then((rows) => (entries = rows))
			.finally(() => (loading = false));
	});

	const models = $derived(new Set(entries.map((e) => e.run?.model).filter(Boolean)).size);

	function patch(i: number, change: Partial<Entry>) {
		entries = entries.map((e, j) => (j === i ? { ...e, ...change } : e));
	}

	async function freeze() {
		freezing = true;
		const ids: string[] = [];
		let failed = false;
		for (let i = 0; i < entries.length; i++) {
			const entry = entries[i];
			if (!entry.run) {
				failed = true;
				continue;
			}
			if (entry.reuse && entry.existing.length) {
				ids.push(entry.existing[0].id);
				continue;
			}
			try {
				const set = await createSlotSet({
					parent_run: entry.runId,
					name: entry.name.slice(0, 200),
					allow_unbuilt_parent: entry.allowUnbuilt,
					selection,
					pool_size: poolSize,
				});
				ids.push(set.id);
				patch(i, { error: '', existing: [set, ...entry.existing], reuse: true });
			} catch (e) {
				failed = true;
				patch(i, { error: formatApiError(e, 'Could not freeze this parent.') });
			}
		}
		freezing = false;
		if (!failed && ids.length) onfrozen(ids);
	}
</script>

{#if loading}
	<LoadingState label="loading parents" />
{:else}
	<div class="space-y-4">
		<p class="text-sm text-muted-foreground">
			{entries.length} parent{entries.length === 1 ? '' : 's'} from {models} model{models === 1 ? '' : 's'} ·
			selection <span class="font-mono">{selection?.rule ?? 'manual'}</span>{#if selection?.seed != null} · seed <span class="font-mono">{selection.seed}</span>{/if}
			{#if entries.length < 3 || models < 2}
				<span class="text-warning-fg">· recommended: ≥ 3 parents from ≥ 2 models (parent is a random effect in pooled analyses)</span>
			{/if}
		</p>
		<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
			{#each entries as entry, i (entry.runId)}
				<Card.Root>
					<Card.Header>
						<Card.Title class="flex flex-wrap items-center gap-2 text-sm">
							<a class="font-mono text-primary hover:underline" href={`/samples/${entry.runId}`} target="_blank" rel="noopener">{shortSha(entry.runId, 8)}</a>
							{#if entry.run?.outcome}<StatusBadge status={entry.run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(entry.run.outcome)} />{/if}
						</Card.Title>
						{#if entry.run}
							<Card.Description class="text-xs">
								{entry.run.labels?.task ?? '—'} · {entry.run.model ?? '—'}
								{#each Object.entries(entry.run.labels ?? {}).filter(([k]) => k !== 'task' && k !== 'model').slice(0, 3) as [, v] (v)} · {v}{/each}
							</Card.Description>
						{/if}
					</Card.Header>
					<Card.Content class="space-y-3 text-sm">
						{#if entry.error}<ErrorAlert message={entry.error} />{/if}
						{#if entry.run}
							<p class="text-xs text-muted-foreground">
								pass rate {fmtRate(entry.run.agent_pass_rate)} · {fmtUsd(entry.run.cost_usd)} ·
								run fingerprint <span class="font-mono">{shortSha(entry.run.identity?.run_fingerprint, 10)}</span>
							</p>
							{#if entry.existing.length}
								<label class="flex items-start gap-2 text-xs">
									<Checkbox checked={entry.reuse} onCheckedChange={(v) => patch(i, { reuse: v === true })} />
									<span>
										Reuse the existing slot set <b>{entry.existing[0].name || shortSha(entry.existing[0].id, 8)}</b>
										({entry.existing.length} set{entry.existing.length === 1 ? '' : 's'} for this parent)
									</span>
								</label>
							{/if}
							{#if !entry.reuse}
								<div class="space-y-1">
									<Label for={`freeze-name-${i}`}>Name</Label>
									<Input id={`freeze-name-${i}`} value={entry.name} oninput={(e) => patch(i, { name: (e.currentTarget as HTMLInputElement).value })} />
								</div>
								{#if entry.run.outcome !== 'tested'}
									<label class="flex items-start gap-2 text-xs">
										<Checkbox checked={entry.allowUnbuilt} onCheckedChange={(v) => patch(i, { allowUnbuilt: v === true })} />
										<span>Allow an unbuilt parent: no runtime slot tests, never in confirmatory studies.</span>
									</label>
								{/if}
							{:else}
								<Badge variant="outline">frozen</Badge>
							{/if}
						{/if}
					</Card.Content>
				</Card.Root>
			{/each}
		</div>
		<div class="flex flex-wrap items-center gap-2">
			{#if onback}<Button variant="outline" size="sm" onclick={onback}>Back to the pool</Button>{/if}
			<Button size="sm" class="ml-auto" disabled={freezing || entries.every((e) => !e.run)} onclick={freeze}>
				{#if freezing}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Snowflake class="mr-1.5 h-3.5 w-3.5" />{/if}
				Freeze and choose parts
			</Button>
		</div>
	</div>
{/if}
