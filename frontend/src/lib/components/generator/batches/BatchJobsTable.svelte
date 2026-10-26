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
	import { rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';
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
		<div class="table-frame md:rounded-md md:border md:border-border md:bg-card" {@attach tableFrame}>
			<table class="table-card-mobile">
				<thead>
					<tr>
						<th scope="col">Task</th>
						<th scope="col">Model</th>
						{#if variantHeader}<th scope="col">{variantHeader}</th>{/if}
						<th scope="col" class="num">Trial</th>
						<th scope="col">Status</th>
						{#if graded}<th scope="col">Evaluation</th>{/if}
						<th scope="col" class="num">Cost</th>
						<th scope="col" class="num hidden lg:table-cell">Duration</th>
						<th scope="col" class="num hidden lg:table-cell">Seed</th>
					</tr>
				</thead>
				<tbody>
					{#each visible as job (job.id)}
						{@const href = `/applications/${job.id}`}
						{@const provider = modelNamePrefix(job.model_name)}
						<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
						<tr class="row-link" onclick={rowLink(href)}>
							<td data-label="Task" class="md:max-w-40">
								<a
									{href}
									class="block min-w-0 break-words font-medium transition-colors hover:text-primary md:truncate"
									title="Open this run ({shortId(job.id)})"
									aria-label="Open run: {job.template_name || 'Task'}, {job.model_name ?? 'model'}{job.repeat_index != null ? `, trial ${job.repeat_index + 1}` : ''}"
								>{job.template_name || 'Task'}</a>
							</td>
							<td data-label="Model" class="md:max-w-44">
								{#if job.model_name}
									<!-- One block so the phone card stacks name over provider instead of squeezing them side by side. -->
									<span class="block min-w-0">
										<span class="block break-words md:truncate" title={job.model_id_str ?? job.model_name}>{shortModelName(job.model_name)}</span>
										{#if provider}<span class="cell-sub block break-words text-xs text-muted-foreground md:truncate">{provider}</span>{/if}
									</span>
								{:else}
									<span class="text-muted-foreground">—</span>
								{/if}
							</td>
							{#if variantHeader}
								<td data-label={variantHeader} class="md:max-w-48">
									<span class="block min-w-0 break-all font-mono text-xs md:truncate md:break-normal" title={variantOf(job)}>{variantOf(job)}</span>
								</td>
							{/if}
							<td data-label="Trial" class="num text-xs max-md:text-left">
								{#if job.repeat_index != null}{job.repeat_index + 1}{:else}<span class="text-muted-foreground">—</span>{/if}
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
									<span class="cell-sub mt-0.5 line-clamp-2 min-w-0 break-words text-[11px] text-danger-fg md:block md:max-w-44 md:truncate" title={job.error_message}>{job.error_message}</span>
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
							<td data-label="Cost" class="num text-xs max-md:text-left">
								{#if job.cost_usd != null}{formatUsd(job.cost_usd)}{:else}<span class="text-muted-foreground">—</span>{/if}
							</td>
							<td data-label="Duration" class="num hidden text-xs max-md:flex max-md:text-left lg:table-cell">
								{#if job.duration_seconds != null}{formatDuration(job.duration_seconds)}{:else}<span class="text-muted-foreground">—</span>{/if}
							</td>
							<td data-label="Seed" class="num hidden text-xs max-md:flex max-md:text-left lg:table-cell">
								{#if job.experiment_seed != null}{job.experiment_seed}{:else}<span class="text-muted-foreground">—</span>{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>
