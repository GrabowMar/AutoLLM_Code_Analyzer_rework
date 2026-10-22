<script lang="ts">
	/**
	 * Every run of a batch as a table (cards below md), in trial order, with a
	 * quick filter for in-progress / failed / flagged runs.
	 *
	 * Props:
	 * - `jobs`: the batch's job list items.
	 * - `mode`: batch mode; agent shows the harness preset, scaffolding the profile.
	 * - `graded`: hides the evaluation column for free-form batches.
	 */
	import type { GenerationJobList } from '$lib/api/generation';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Segmented } from '$lib/components/ui/segmented';
	import { formatDuration } from '$lib/utils/formatters';
	import ListFilter from '@lucide/svelte/icons/list-filter';
	import {
		formatPassRate,
		formatUsd,
		jobDisplayStatus,
		jobPhaseTones,
		presetRefLabel,
		shortId,
	} from './batch-utils';

	interface Props {
		jobs: GenerationJobList[];
		mode: string;
		graded: boolean;
	}

	let { jobs, mode, graded }: Props = $props();

	// 'all' | 'active' | 'failed' | 'flagged' (string: Segmented binds strings).
	let filter = $state('all');

	const isActive = (j: GenerationJobList) => j.status === 'pending' || j.status === 'running';
	const isFailed = (j: GenerationJobList) => j.status === 'failed' || j.status === 'cancelled';
	const isFlagged = (j: GenerationJobList) => j.validity === 'warn' || j.validity === 'exclude';

	const sorted = $derived(
		[...jobs].sort(
			(a, b) =>
				(a.repeat_index ?? Number.MAX_SAFE_INTEGER) - (b.repeat_index ?? Number.MAX_SAFE_INTEGER) ||
				(a.template_name ?? '').localeCompare(b.template_name ?? '') ||
				(a.model_name ?? '').localeCompare(b.model_name ?? '') ||
				a.created_at.localeCompare(b.created_at)
		)
	);

	const counts = $derived({
		all: jobs.length,
		active: jobs.filter(isActive).length,
		failed: jobs.filter(isFailed).length,
		flagged: jobs.filter(isFlagged).length,
	});

	const visible = $derived(
		filter === 'active'
			? sorted.filter(isActive)
			: filter === 'failed'
				? sorted.filter(isFailed)
				: filter === 'flagged'
					? sorted.filter(isFlagged)
					: sorted
	);

	const filterItems = $derived([
		{ value: 'all', label: `All ${counts.all}` },
		{ value: 'active', label: `In progress ${counts.active}` },
		{ value: 'failed', label: `Failed ${counts.failed}` },
		{ value: 'flagged', label: `Flagged ${counts.flagged}`, title: 'Validity warnings or excluded trials' },
	]);

	const variantHeader = $derived(mode === 'agent' ? 'Harness preset' : mode === 'scaffolding' ? 'Profile' : '');

	function variantOf(job: GenerationJobList): string {
		if (mode === 'agent') return presetRefLabel(job.harness_preset) || job.harness || '—';
		if (mode === 'scaffolding') return job.profile_ref || 'Auto';
		return '';
	}

	const evalTones = { not_applicable: 'neutral', infra_error: 'warning' } as const;
</script>

<div class="space-y-3">
	<Segmented aria-label="Filter runs" size="sm" items={filterItems} bind:value={filter} />

	{#if visible.length === 0}
		<EmptyState compact icon={ListFilter} title="No runs match this filter" />
	{:else}
		<div class="relative overflow-x-auto md:rounded-md md:border md:border-border">
			<table class="table-card-mobile w-full md:[&_td]:px-2 md:[&_th]:px-2">
				<thead>
					<tr>
						<th scope="col">Task</th>
						<th scope="col">Model</th>
						{#if variantHeader}<th scope="col">{variantHeader}</th>{/if}
						<th scope="col">Trial</th>
						<th scope="col">Status</th>
						{#if graded}<th scope="col">Evaluation</th>{/if}
						<th scope="col" class="text-right">Cost</th>
						<th scope="col" class="hidden text-right lg:table-cell">Duration</th>
						<th scope="col" class="hidden text-right lg:table-cell">Seed</th>
					</tr>
				</thead>
				<tbody>
					{#each visible as job (job.id)}
						<tr>
							<td data-label="Task" class="max-w-40">
								<a
									href="/applications/{job.id}"
									class="block truncate font-medium transition-colors hover:text-primary hover:underline"
									title="Open this run ({shortId(job.id)})"
									aria-label="Open run: {job.template_name || 'Task'}, {job.model_name ?? 'model'}{job.repeat_index != null ? `, trial ${job.repeat_index + 1}` : ''}"
								>{job.template_name || 'Task'}</a>
							</td>
							<td data-label="Model" class="max-w-44">
								<span class="block truncate" title={job.model_id_str ?? ''}>{job.model_name ?? '—'}</span>
							</td>
							{#if variantHeader}
								<td data-label={variantHeader} class="max-w-48">
									<span class="block truncate font-mono text-xs">{variantOf(job)}</span>
								</td>
							{/if}
							<td data-label="Trial" class="font-mono text-xs tabular-nums">
								{job.repeat_index != null ? job.repeat_index + 1 : '—'}
							</td>
							<td data-label="Status">
								<span class="inline-flex flex-wrap items-center gap-1">
									<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
									{#if job.validity === 'exclude'}
										<span title="Excluded from analyses (validity)"><StatusBadge status="excluded" tone="danger" /></span>
									{:else if job.validity === 'warn'}
										<span title="Valid, with validity warnings"><StatusBadge status="warn" tone="warning" label="warning" /></span>
									{/if}
								</span>
								{#if job.error_message}
									<span class="mt-0.5 block max-w-44 truncate text-[11px] text-danger-fg" title={job.error_message}>{job.error_message}</span>
								{/if}
							</td>
							{#if graded}
								<td data-label="Evaluation">
									{#if job.eval_status}
										<span class="inline-flex items-center gap-1.5">
											<StatusBadge status={job.eval_status} tones={evalTones} />
											{#if job.pass_rate != null}
												<span class="font-mono text-xs tabular-nums">{formatPassRate(job.pass_rate)}</span>
											{/if}
										</span>
									{:else}
										<span class="text-muted-foreground">—</span>
									{/if}
								</td>
							{/if}
							<td data-label="Cost" class="text-right font-mono text-xs tabular-nums">{formatUsd(job.cost_usd)}</td>
							<td data-label="Duration" class="hidden whitespace-nowrap text-right lg:table-cell font-mono text-xs tabular-nums">
								{formatDuration(job.duration_seconds)}
							</td>
							<td data-label="Seed" class="hidden text-right lg:table-cell font-mono text-xs tabular-nums">{job.experiment_seed ?? '—'}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
