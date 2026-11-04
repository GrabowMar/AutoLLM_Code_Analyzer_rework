<script lang="ts">
	/**
	 * StopRulesEditor — `study.stop_rules` (section 4.6) as a compact table: code, scope, window,
	 * threshold, action and stages per rule; `ci_width_reached` adds features, n_min and n_max.
	 * Emits the whole list on every edit.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import type { StopRule } from '$lib/api/v3/types';
	import NumberInput from './NumberInput.svelte';
	import ListInput from './ListInput.svelte';

	interface Props {
		value: StopRule[];
		readonly?: boolean;
		onchange: (rules: StopRule[]) => void;
	}

	let { value, readonly = false, onchange }: Props = $props();

	const CODES: StopRule['code'][] = [
		'infra_error_rate',
		'no_provider_rate',
		'output_invalid_rate',
		'consecutive_failures',
		'cost_overrun',
		'spend_fraction',
		'condition_error_rate',
		'ci_width_reached',
		'wall_clock_total',
	];
	const SCOPES: StopRule['scope'][] = ['study', 'condition', 'unit'];
	const ACTIONS: StopRule['action'][] = ['pause', 'stop', 'pause_condition', 'alert'];
	const items = (xs: string[]) => xs.map((x) => ({ value: x, label: x.replace(/_/g, ' ') }));
	// The `study` scope is an experiment in the UI (the stored value keeps the API name).
	const scopeItems = SCOPES.map((x) => ({ value: x, label: x === 'study' ? 'experiment' : x }));

	function update(i: number, patch: Partial<StopRule>) {
		onchange(value.map((r, j) => (j === i ? { ...r, ...patch } : r)));
	}

	function toggleStage(i: number, stage: 'pilot' | 'main', on: boolean) {
		const stages = new Set(value[i].stages);
		if (on) stages.add(stage);
		else stages.delete(stage);
		update(i, { stages: (['pilot', 'main'] as const).filter((s) => stages.has(s)) });
	}

	function add() {
		onchange([
			...value,
			{ code: 'infra_error_rate', scope: 'study', window: 20, threshold: 0.3, action: 'pause', stages: ['pilot', 'main'], features: [], n_min: 10, n_max: null },
		]);
	}
</script>

<div class="space-y-2">
	{#if value.length === 0}
		<p class="text-xs text-muted-foreground">No stop rules: only the budget caps stop the experiment.</p>
	{/if}
	{#each value as rule, i (i)}
		<div class="space-y-2 rounded-md border border-border p-2">
			<div class="grid gap-2 sm:grid-cols-[minmax(0,12rem)_minmax(0,8rem)_minmax(0,9rem)_auto]">
				<Select.Simple value={rule.code} items={items(CODES)} size="sm" disabled={readonly} aria-label="Rule" onValueChange={(v) => update(i, { code: v as StopRule['code'] })} />
				<Select.Simple value={rule.scope} items={scopeItems} size="sm" disabled={readonly} aria-label="Scope" onValueChange={(v) => update(i, { scope: v as StopRule['scope'] })} />
				<Select.Simple value={rule.action} items={items(ACTIONS)} size="sm" disabled={readonly} aria-label="Action" onValueChange={(v) => update(i, { action: v as StopRule['action'] })} />
				{#if !readonly}
					<Button variant="ghost" size="icon" aria-label="Remove rule" onclick={() => onchange(value.filter((_, j) => j !== i))}><Trash2 /></Button>
				{/if}
			</div>
			<div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
				<label class="flex items-center gap-1.5">
					window
					<NumberInput value={rule.window} min={1} step={1} {readonly} class="w-20" onchange={(n) => n !== null && update(i, { window: Math.max(1, Math.round(n)) })} />
				</label>
				<label class="flex items-center gap-1.5">
					threshold
					<NumberInput value={rule.threshold} step={0.01} {readonly} class="w-24" onchange={(n) => n !== null && update(i, { threshold: n })} />
				</label>
				{#each ['pilot', 'main'] as const as stage (stage)}
					<label class="flex items-center gap-1.5">
						<Checkbox checked={rule.stages.includes(stage)} disabled={readonly} onCheckedChange={(c) => toggleStage(i, stage, c === true)} />
						{stage}
					</label>
				{/each}
			</div>
			{#if rule.code === 'ci_width_reached'}
				<div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
					<label class="flex min-w-48 flex-1 items-center gap-1.5">
						features
						<ListInput value={rule.features} {readonly} placeholder="primary features" onchange={(v) => update(i, { features: v })} />
					</label>
					<label class="flex items-center gap-1.5">
						n min
						<NumberInput value={rule.n_min} min={1} step={1} {readonly} class="w-20" onchange={(n) => n !== null && update(i, { n_min: Math.max(1, Math.round(n)) })} />
					</label>
					<label class="flex items-center gap-1.5">
						n max
						<NumberInput value={rule.n_max} min={1} step={1} {readonly} placeholder="repeats" class="w-24" onchange={(n) => update(i, { n_max: n === null ? null : Math.max(1, Math.round(n)) })} />
					</label>
				</div>
			{/if}
		</div>
	{/each}
	{#if !readonly}
		<Button variant="outline" size="sm" onclick={add}><Plus />Add stop rule</Button>
	{/if}
</div>
