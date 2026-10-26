<script lang="ts">
	/**
	 * Tool behaviour across every (non-canary) execution: outcomes, failure
	 * reasons, success rate of the executions that actually ran on files,
	 * findings of the succeeded scans only, durations and versions.
	 */
	import type { ReportToolBehaviourRow } from '$lib/api/reports';
	import StatusCountsBar from '../StatusCountsBar.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import MaybeValue from '../MaybeValue.svelte';
	import { fmtNum, fmtSeconds, fmtShare, humanize } from '../format';

	interface Props {
		tools: ReportToolBehaviourRow[];
	}

	let { tools }: Props = $props();

	function successClass(v: number): string {
		if (v >= 0.95) return 'text-success-fg';
		if (v >= 0.8) return 'text-warning-fg';
		return 'text-danger-fg';
	}
</script>

<div class="table-scroll-wrapper">
	<table class="table-card-mobile md:min-w-[60rem]">
		<thead>
			<tr>
				<th scope="col">Tool</th>
				<th scope="col" class="w-60">Executions</th>
				<th scope="col" class="text-right" title="Succeeded / executions that ran on files"
					>Success</th
				>
				<th
					scope="col"
					class="text-right"
					title="Findings of succeeded scans (counted in the measurement)">Findings</th
				>
				<th scope="col" class="text-right">Per scan</th>
				<th scope="col" class="text-right">Mean time</th>
				<th scope="col">Failure reasons</th>
				<th scope="col">Versions</th>
			</tr>
		</thead>
		<tbody>
			{#each tools as t (t.analyzer)}
				{@const reasons = Object.entries(t.failure_reasons ?? {})}
				<tr>
					<td data-label="" class="font-mono text-xs font-medium">{t.analyzer}</td>
					<td data-label="Executions">
						<div class="text-[11px] text-muted-foreground">
							{t.executions} total · {t.scans} scans
						</div>
						<StatusCountsBar counts={t.statuses} />
					</td>
					<td data-label="Success" class="font-mono text-xs tabular-nums md:text-right">
						{#if t.success_rate != null}
							<span class={successClass(t.success_rate)}>{fmtShare(t.success_rate, 0)}</span>
						{:else}
							<NaValue
								reason="No execution ran on files (all not applicable, skipped or cancelled)"
							/>
						{/if}
					</td>
					<td data-label="Findings" class="font-mono text-xs tabular-nums md:text-right">
						{#if t.scans > 0}
							{t.total}
							<span class="block text-[11px] text-muted-foreground">{t.counted} counted</span>
						{:else}
							<NaValue reason="No succeeded scan: no findings could be produced" />
						{/if}
					</td>
					<td data-label="Per scan" class="font-mono text-xs tabular-nums md:text-right">
						<MaybeValue
							value={fmtNum(t.findings_per_scan, 2)}
							reason="No succeeded scan to average over"
						/>
					</td>
					<td data-label="Mean time" class="font-mono text-xs tabular-nums md:text-right">
						<MaybeValue
							value={fmtSeconds(t.mean_duration_s)}
							reason="No succeeded scan recorded a duration"
						/>
					</td>
					<td data-label="Failure reasons" class="text-[11px]">
						{#if reasons.length === 0}
							<span class="text-muted-foreground">none</span>
						{:else}
							<span class="flex flex-wrap gap-1">
								{#each reasons as [reason, n] (reason)}
									<span class="mono-tag">{humanize(reason)} ×{n}</span>
								{/each}
							</span>
						{/if}
					</td>
					<td data-label="Versions" class="font-mono text-[11px] text-muted-foreground">
						{#if t.versions.length}{t.versions.join(', ')}{:else}<NaValue
								reason="No execution recorded a tool version"
							/>{/if}
					</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>
