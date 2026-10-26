<script lang="ts">
	/**
	 * Per-tool execution outcome inside the measured runs of a report. A tool
	 * that failed or timed out produced no data for that run — never "clean".
	 */
	import type { ReportToolExecutionRow } from '$lib/api/reports';
	import StatusCountsBar from '../StatusCountsBar.svelte';
	import NaValue from '$lib/components/analysis/NaValue.svelte';

	interface Props {
		tools: ReportToolExecutionRow[];
	}

	let { tools }: Props = $props();
</script>

<div class="table-scroll-wrapper">
	<table class="table-card-mobile md:min-w-[40rem]">
		<thead>
			<tr>
				<th scope="col">Tool</th>
				<th scope="col" class="text-right">Executions</th>
				<th scope="col" class="w-64">Outcomes</th>
				<th scope="col" class="text-right">Succeeded</th>
				<th scope="col">Versions</th>
			</tr>
		</thead>
		<tbody>
			{#each tools as t (t.analyzer)}
				<tr>
					<td data-label="" class="font-mono text-xs font-medium">{t.analyzer}</td>
					<td data-label="Executions" class="font-mono text-xs tabular-nums md:text-right"
						>{t.tasks}</td
					>
					<td data-label="Outcomes"><StatusCountsBar counts={t.statuses} /></td>
					<td data-label="Succeeded" class="font-mono text-xs tabular-nums md:text-right">
						{t.succeeded}<span class="text-muted-foreground">/{t.tasks}</span>
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
