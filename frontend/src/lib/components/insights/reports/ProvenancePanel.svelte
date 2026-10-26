<script lang="ts">
	/**
	 * Where a report's numbers come from: the analysis plan, the canonical runs
	 * (each linking to its run page), scoring version, severity weights and
	 * eligibility rules, plus finding exports for the plan.
	 */
	import Download from '@lucide/svelte/icons/download';
	import { Button } from '$lib/components/ui/button';
	import type { ReportProvenance } from '$lib/api/reports';
	import { findingsExportUrl } from '$lib/api/export';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import PlanHash from '../PlanHash.svelte';
	import { formatTimestamp, humanize, runHref } from '../format';

	interface Props {
		provenance: ReportProvenance;
	}

	let { provenance }: Props = $props();

	const runIds = $derived(provenance.run_ids ?? []);
	const rules = $derived(Object.entries(provenance.exclusion_rules ?? {}));
	const weights = $derived(Object.entries(provenance.severity_weights ?? {}));
	const SEV_ORDER = ['critical', 'high', 'medium', 'low', 'info'];
	const sortedWeights = $derived(
		[...weights].sort((a, b) => SEV_ORDER.indexOf(a[0]) - SEV_ORDER.indexOf(b[0]))
	);
</script>

<div class="space-y-4 text-xs">
	<dl class="grid gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
		<div class="min-w-0">
			<dt class="section-label">Analysis plan</dt>
			<dd class="mt-1 flex flex-wrap items-center gap-1.5">
				{#if provenance.plan_hash}
					<PlanHash hash={provenance.plan_hash} />
					<span class="text-muted-foreground"
						>{provenance.plan_explicit ? 'pinned' : 'auto-selected'}</span
					>
				{:else if provenance.scope}
					<span class="text-muted-foreground">{provenance.scope}</span>
				{:else}
					<NaValue reason="Nothing was measured under any plan" />
				{/if}
			</dd>
		</div>
		<div>
			<dt class="section-label">Generated</dt>
			<dd class="mt-1">{formatTimestamp(provenance.generated_at)}</dd>
		</div>
		<div>
			<dt class="section-label">Schema · scoring</dt>
			<dd class="mt-1 flex flex-wrap items-center gap-1.5">
				<span class="mono-tag">report v{provenance.report_schema_version}</span>
				{#if provenance.scoring_version}<span class="mono-tag">{provenance.scoring_version}</span
					>{/if}
				{#if provenance.complete_only}<span class="text-muted-foreground">complete runs only</span
					>{/if}
			</dd>
		</div>
		{#if sortedWeights.length > 0}
			<div>
				<dt class="section-label">Severity weights</dt>
				<dd class="mt-1 font-mono tabular-nums">
					{sortedWeights.map(([k, v]) => `${k} ${v}`).join(' · ')}
				</dd>
			</div>
		{/if}
		{#if rules.length > 0}
			<div class="sm:col-span-2">
				<dt class="section-label">Eligibility rules</dt>
				<dd class="mt-1 flex flex-wrap gap-1.5">
					{#each rules as [key, value] (key)}
						<span class="mono-tag">
							{humanize(key)}: {typeof value === 'boolean' ? (value ? 'yes' : 'no') : value}
						</span>
					{/each}
				</dd>
			</div>
		{/if}
	</dl>

	{#if provenance.plan_hash || runIds.length > 0}
		<div class="space-y-2 border-t pt-3">
			{#if runIds.length > 0}
				<details class="group">
					<summary class="cursor-pointer select-none font-medium hover:text-foreground">
						{runIds.length} canonical run{runIds.length === 1 ? '' : 's'} behind the numbers
					</summary>
					<ul class="mt-2 grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
						{#each runIds as id (id)}
							<li>
								<a href={runHref(id)} class="mono-tag hover:border-primary/50 hover:text-foreground"
									>{id}</a
								>
							</li>
						{/each}
					</ul>
				</details>
			{/if}
			{#if provenance.plan_hash}
				<div class="flex flex-wrap items-center gap-2">
					<span class="text-muted-foreground">Findings of every canonical run under this plan:</span
					>
					<Button
						variant="outline"
						size="sm"
						href={findingsExportUrl('csv', { plan_hash: provenance.plan_hash, canonical: true })}
						download
					>
						<Download class="h-3.5 w-3.5" />CSV
					</Button>
					<Button
						variant="outline"
						size="sm"
						href={findingsExportUrl('sarif', { plan_hash: provenance.plan_hash, canonical: true })}
						download
					>
						<Download class="h-3.5 w-3.5" />SARIF
					</Button>
				</div>
			{/if}
		</div>
	{/if}
</div>
