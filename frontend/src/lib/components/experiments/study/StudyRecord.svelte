<script lang="ts">
	/**
	 * StudyRecord — the Abstract's side panel, like an article's record page:
	 * accession id, dates and duration, the design in counts, baseline, seed,
	 * run order, spend, provenance, and a copyable reference line.
	 *
	 * Props:
	 * - `experiment`: the detail.
	 * - `costUsd`: spend so far (live rollup; falls back to the detail's).
	 * - `policyEffective`: the launched run order per endpoint.
	 */
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import type { ExperimentDetail } from '$lib/api/types/experiments';
	import { formatUsd } from '$lib/components/generator/batches/batch-utils';
	import Copy from '@lucide/svelte/icons/copy';
	import { MODE_LABELS, conditionName, effectivePolicyLabel, formatEta, plural, policyLabel } from '../format';
	import { designShape, shortExperimentId } from './findings';

	interface Props {
		experiment: ExperimentDetail;
		costUsd?: number;
		policyEffective?: Record<string, string> | string | null;
	}

	let { experiment, costUsd, policyEffective = null }: Props = $props();

	const shape = $derived(designShape(experiment));
	const baseline = $derived(experiment.conditions.find((c) => c.id === experiment.baseline_condition_id) ?? null);
	const accession = $derived(shortExperimentId(experiment.id));
	const cost = $derived(costUsd ?? experiment.cost_usd ?? 0);

	const dateFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
	const dateTimeFmt = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
	const day = (iso: string | null) => (iso ? dateFmt.format(new Date(iso)) : '—');
	const stamp = (iso: string | null) => (iso ? dateTimeFmt.format(new Date(iso)) : '—');

	const duration = $derived.by(() => {
		const { launched_at: start, completed_at: end } = experiment;
		if (!start) return null;
		const until = end ? new Date(end).getTime() : Date.now();
		return formatEta((until - new Date(start).getTime()) / 1000);
	});

	const reference = $derived(
		`${accession}. “${experiment.name}”. LLM Lab experiment, ${day(experiment.launched_at ?? experiment.created_at)}. ${page.url.origin}/experiments/${experiment.id}`
	);

	async function copy(text: string, what: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success(`${what} copied`);
		} catch {
			toast.error('Could not copy to the clipboard');
		}
	}

	const rows = $derived.by(() => {
		const e = experiment;
		const list: { label: string; value: string; mono?: boolean; title?: string }[] = [
			{ label: 'Method', value: MODE_LABELS[e.mode] ?? e.mode },
			{ label: 'Registered', value: day(e.created_at), title: new Date(e.created_at).toLocaleString() },
		];
		if (e.launched_at) list.push({ label: 'Launched', value: stamp(e.launched_at), title: new Date(e.launched_at).toLocaleString() });
		if (e.completed_at) list.push({ label: 'Finished', value: stamp(e.completed_at), title: new Date(e.completed_at).toLocaleString() });
		if (duration) list.push({ label: e.completed_at ? 'Duration' : 'Running for', value: duration, mono: true });
		list.push(
			{ label: 'Conditions', value: `${shape.conditions}`, mono: true, title: `${plural(shape.models, 'model')} × ${plural(shape.variants, 'variant')}${shape.ablations ? ` + ${plural(shape.ablations, 'ablation')}` : ''}` },
			{ label: 'Apps × repeats', value: `${shape.apps} × ${shape.repeats}`, mono: true },
			{ label: 'Cells', value: shape.cells.toLocaleString(), mono: true },
			{ label: 'Baseline', value: baseline ? conditionName(baseline) : 'None' },
			{ label: 'Stack', value: e.stack_slug || 'Per condition profile', mono: !!e.stack_slug },
			{ label: 'Base seed', value: e.base_seed != null ? String(e.base_seed) : 'Random per run', mono: e.base_seed != null },
			{
				label: 'Run order',
				value: e.status === 'draft' ? policyLabel(e.scheduling_policy) : effectivePolicyLabel(policyEffective, e.scheduling_policy),
			}
		);
		if (cost > 0) list.push({ label: 'Spend', value: formatUsd(cost), mono: true, title: 'OpenRouter usage; local endpoints cost nothing here' });
		return list;
	});
</script>

<aside class="overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)]" aria-labelledby="study-record-title">
	<div class="border-b border-border/60 px-4 py-2.5">
		<p class="section-label">Record</p>
		<h3 id="study-record-title" class="flex items-center gap-2 font-mono text-sm font-medium tracking-wide">
			{accession}
			<button
				type="button"
				class="inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
				title="Copy the experiment id ({experiment.id})"
				aria-label="Copy the experiment id"
				onclick={() => copy(experiment.id, 'Experiment id')}
			>
				<Copy class="size-3.5" aria-hidden="true" />
			</button>
		</h3>
	</div>
	<dl class="divide-y divide-border/50 text-sm">
		{#each rows as row (row.label)}
			<div class="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 px-4 py-1.5">
				<dt class="text-xs leading-5 text-muted-foreground">{row.label}</dt>
				<dd class={row.mono ? 'font-mono text-xs leading-5 tabular-nums' : 'leading-5'} title={row.title}>
					<span class="line-clamp-2 break-words">{row.value}</span>
				</dd>
			</div>
		{/each}
		{#if experiment.cloned_from}
			<div class="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 px-4 py-1.5">
				<dt class="text-xs leading-5 text-muted-foreground">Cloned from</dt>
				<dd class="leading-5"><a class="settings-link" href="/experiments/{experiment.cloned_from.id}">{experiment.cloned_from.name}</a></dd>
			</div>
		{/if}
		{#if experiment.created_by_schedule}
			<div class="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3 px-4 py-1.5">
				<dt class="text-xs leading-5 text-muted-foreground">Created by</dt>
				<dd class="leading-5">Schedule #{experiment.created_by_schedule.id}</dd>
			</div>
		{/if}
	</dl>
	<div class="space-y-1.5 border-t border-border/60 bg-muted/30 px-4 py-2.5">
		<div class="flex items-center justify-between gap-2">
			<p class="section-label">Reference</p>
			<button
				type="button"
				class="inline-flex min-h-7 cursor-pointer items-center gap-1 rounded-md px-1.5 text-xs text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
				onclick={() => copy(reference, 'Reference')}
			>
				<Copy class="size-3.5" aria-hidden="true" />Copy
			</button>
		</div>
		<p class="break-words font-display text-[13px] leading-snug text-foreground/90">{reference}</p>
	</div>
</aside>
