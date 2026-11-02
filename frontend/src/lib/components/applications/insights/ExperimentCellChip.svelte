<script lang="ts">
	/**
	 * ExperimentCellChip — where this sample sits in its study design:
	 * "experiment › condition › trial k/N", linking to the experiment page.
	 * Descriptive only (no interim statistics here).
	 *
	 * Props: `cell` (timeline.experiment; renders nothing when null).
	 */
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import type { ExperimentCell } from '$lib/api/job-insights';

	interface Props {
		cell: ExperimentCell | null | undefined;
	}

	let { cell }: Props = $props();

	const trial = $derived(
		cell?.repeat_number != null ? `trial ${cell.repeat_number}${cell.planned_repeats ? `/${cell.planned_repeats}` : ''}` : null
	);
	const title = $derived.by(() => {
		if (!cell) return '';
		const parts = [
			`Experiment: ${cell.experiment_name ?? cell.experiment_id}`,
			cell.condition_label ? `Condition: ${cell.condition_label}` : null,
			cell.app_slug ? `App: ${cell.app_slug}` : null,
			trial ? `Trial ${cell.repeat_number} of ${cell.planned_repeats ?? '?'} planned` : null,
			cell.cell_jobs > 1 ? `${cell.cell_jobs} jobs in this cell (retries / reruns)${cell.is_current_in_cell ? '' : '; a newer one replaced this job'}` : null,
		];
		return parts.filter(Boolean).join('\n');
	});
</script>

{#if cell}
	<a
		href="/experiments/{cell.experiment_id}"
		class="inline-flex min-w-0 max-w-full items-center gap-1 hover:text-primary"
		{title}
	>
		<FlaskConical class="size-3 shrink-0" aria-hidden="true" />
		<span class="truncate">{cell.experiment_name ?? 'Experiment'}</span>
		{#if cell.condition_label}<span class="opacity-50" aria-hidden="true">›</span><span class="max-w-48 truncate">{cell.condition_label}</span>{/if}
		{#if trial}<span class="opacity-50" aria-hidden="true">›</span><span class="font-mono tabular-nums">{trial}</span>{/if}
		{#if !cell.is_current_in_cell}<span class="text-warning-fg">· superseded</span>{/if}
	</a>
{/if}
