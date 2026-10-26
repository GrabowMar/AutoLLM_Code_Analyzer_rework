<script lang="ts">
	/**
	 * Measured SLOC per job with links to the job and its canonical run, so
	 * every LOC total traces back to the run that measured it.
	 */
	import type { ReportLocBlock } from '$lib/api/reports';
	import MaybeValue from '../MaybeValue.svelte';
	import { fmtInt, jobHref, runHref } from '../format';

	interface Props {
		loc: ReportLocBlock;
	}

	let { loc }: Props = $props();

	let expanded = $state(false);
	const LIMIT = 10;
	const rows = $derived(expanded ? loc.per_job : loc.per_job.slice(0, LIMIT));
</script>

<div class="space-y-2">
	<dl class="flex flex-wrap gap-x-6 gap-y-1 text-xs">
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Measured SLOC (model-written)</dt>
			<dd class="font-mono font-medium tabular-nums">
				<MaybeValue value={fmtInt(loc.measured_loc)} reason="No measured run" />
			</dd>
		</div>
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Included SLOC</dt>
			<dd class="font-mono font-medium tabular-nums">
				<MaybeValue value={fmtInt(loc.total_loc)} reason="No measured run" />
			</dd>
		</div>
		<div class="flex gap-1.5">
			<dt class="text-muted-foreground">Jobs</dt>
			<dd class="font-mono font-medium tabular-nums">{loc.counted}</dd>
		</div>
	</dl>
	{#if loc.per_job.length > 0}
		<div class="table-scroll-wrapper">
			<table class="min-w-[32rem] text-xs">
				<thead>
					<tr>
						<th scope="col">Job</th>
						<th scope="col">Canonical run</th>
						<th scope="col" class="text-right">Measured SLOC</th>
						<th scope="col" class="text-right">Included SLOC</th>
					</tr>
				</thead>
				<tbody>
					{#each rows as r (r.run_id)}
						<tr>
							<td
								><a href={jobHref(r.job_id)} class="font-mono hover:underline"
									>{r.job_id.slice(0, 8)}</a
								></td
							>
							<td
								><a href={runHref(r.run_id)} class="font-mono hover:underline"
									>{r.run_id.slice(0, 8)}</a
								></td
							>
							<td class="text-right font-mono tabular-nums">
								<MaybeValue value={fmtInt(r.measured_loc)} reason="Not in the measurement" />
							</td>
							<td class="text-right font-mono tabular-nums">
								<MaybeValue value={fmtInt(r.total_loc)} reason="Not in the measurement" />
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		{#if loc.per_job.length > LIMIT}
			<button
				type="button"
				class="cursor-pointer text-xs text-primary underline-offset-2 hover:underline"
				onclick={() => (expanded = !expanded)}
				aria-expanded={expanded}
			>
				{expanded ? 'Show fewer' : `Show all ${loc.per_job.length} jobs`}
			</button>
		{/if}
	{/if}
</div>
