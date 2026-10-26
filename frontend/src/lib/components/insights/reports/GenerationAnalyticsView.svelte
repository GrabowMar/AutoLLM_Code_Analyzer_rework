<script lang="ts">
	/** Typed view of a `generation_analytics` report (schema v2). */
	import type { GenerationAnalyticsReportData } from '$lib/api/reports';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import Kpi from '../Kpi.svelte';
	import MaybeValue from '../MaybeValue.svelte';
	import CoverageNotice from '../CoverageNotice.svelte';
	import { fmtInt, fmtSeconds, fmtShare, fmtUsd, formatTimestamp, jobHref } from '../format';
	import ReportSection from './ReportSection.svelte';

	interface Props {
		data: GenerationAnalyticsReportData;
	}

	let { data }: Props = $props();

	const s = $derived(data.summary);
	const loc = $derived(data.loc);
	let showJobs = $state(false);
	const LIMIT = 10;
	const jobRows = $derived(showJobs ? loc.per_job : loc.per_job.slice(0, LIMIT));
</script>

<div class="space-y-6">
	<p class="text-xs text-muted-foreground">
		Window: last {data.window_days} days (since {formatTimestamp(data.since)}).
	</p>

	<div class="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
		<Kpi
			label="Jobs"
			value={fmtInt(s.total_jobs)}
			sub="{s.completed_jobs} completed · {s.failed_jobs} failed"
		/>
		<Kpi
			label="Success rate"
			value={fmtShare(s.success_rate, 0)}
			reason="No job has finished"
			sub="of finished jobs"
		/>
		<Kpi label="Truncated" value={fmtInt(s.truncated_jobs)} sub="completed but cut off" />
		<Kpi
			label="Avg duration"
			value={fmtSeconds(s.avg_duration)}
			reason="No job recorded a duration"
		/>
		<Kpi label="Cost" value={fmtUsd(s.total_cost)} reason="No job recorded a cost" />
		<Kpi
			label="Measured SLOC"
			value={fmtInt(loc.measured_loc)}
			reason="No completed job could be resolved"
			sub="{loc.counted} jobs · {loc.unavailable} unavailable"
		/>
	</div>

	{#if loc.unavailable > 0}
		<CoverageNotice
			title="{loc.unavailable} completed job{loc.unavailable === 1
				? ''
				: 's'} could not be resolved"
			items={[
				'Their code could not be read, so they are left out of the LOC totals (unavailable, not 0 lines).'
			]}
		/>
	{/if}

	{#if Object.keys(s.by_status ?? {}).length > 0}
		<div class="flex flex-wrap items-center gap-2 text-xs">
			<span class="section-label">By status</span>
			{#each Object.entries(s.by_status) as [status, n] (status)}
				<StatusBadge {status} label="{status.replace(/_/g, ' ')} {n}" />
			{/each}
		</div>
	{/if}

	<ReportSection id="models" title="By model" flush>
		{#if data.by_model.length === 0}
			<p class="px-4 text-xs text-muted-foreground">No generation job in this window.</p>
		{:else}
			<div class="table-scroll-wrapper">
				<table class="min-w-[36rem] text-xs">
					<thead>
						<tr>
							<th scope="col">Model</th>
							<th scope="col" class="text-right">Jobs</th>
							<th scope="col" class="text-right">Completed</th>
							<th scope="col" class="text-right">Success</th>
							<th scope="col" class="text-right">Avg duration</th>
						</tr>
					</thead>
					<tbody>
						{#each data.by_model as m (m.model_id ?? 'none')}
							<tr>
								<td>
									{#if m.model_id}
										<a
											href="/models/{encodeURIComponent(m.model_id)}"
											class="font-medium hover:underline">{m.model_name ?? m.model_id}</a
										>
									{:else}
										<span class="text-muted-foreground">No model</span>
									{/if}
								</td>
								<td class="text-right font-mono tabular-nums">{m.jobs}</td>
								<td class="text-right font-mono tabular-nums">{m.completed}</td>
								<td class="text-right font-mono tabular-nums">
									<MaybeValue value={fmtShare(m.success_rate, 0)} reason="No job has finished" />
								</td>
								<td class="text-right font-mono tabular-nums">
									<MaybeValue
										value={fmtSeconds(m.avg_duration)}
										reason="No job recorded a duration"
									/>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</ReportSection>

	<ReportSection
		id="loc"
		title="Lines of code"
		description="Resolved with the analysis sample resolver: lockfiles, build output and assets excluded."
	>
		<dl class="mb-3 flex flex-wrap gap-x-6 gap-y-1 text-xs">
			{#each [['Backend', loc.backend_loc], ['Frontend', loc.frontend_loc], ['Included', loc.total_loc], ['Measured (model-written)', loc.measured_loc]] as [label, value] (label)}
				<div class="flex gap-1.5">
					<dt class="text-muted-foreground">{label}</dt>
					<dd class="font-mono font-medium tabular-nums">
						<MaybeValue
							value={fmtInt(value as number | null)}
							reason="No resolvable completed job"
						/>
					</dd>
				</div>
			{/each}
		</dl>
		{#if loc.per_job.length > 0}
			<div class="table-scroll-wrapper">
				<table class="min-w-[36rem] text-xs">
					<thead>
						<tr>
							<th scope="col">Job</th>
							<th scope="col" class="text-right">Backend</th>
							<th scope="col" class="text-right">Frontend</th>
							<th scope="col" class="text-right">Included</th>
							<th scope="col" class="text-right">Measured</th>
						</tr>
					</thead>
					<tbody>
						{#each jobRows as j (j.job_id)}
							<tr>
								<td
									><a href={jobHref(j.job_id)} class="font-mono hover:underline"
										>{j.job_id.slice(0, 8)}</a
									></td
								>
								{#each [j.backend_loc, j.frontend_loc, j.total_loc, j.measured_loc] as v, i (i)}
									<td class="text-right font-mono tabular-nums">
										<MaybeValue value={fmtInt(v)} reason="Job could not be resolved" />
									</td>
								{/each}
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#if loc.per_job.length > LIMIT}
				<button
					type="button"
					class="mt-2 cursor-pointer text-xs text-primary underline-offset-2 hover:underline"
					onclick={() => (showJobs = !showJobs)}
					aria-expanded={showJobs}
				>
					{showJobs ? 'Show fewer' : `Show all ${loc.per_job.length} jobs`}
				</button>
			{/if}
		{/if}
	</ReportSection>
</div>
