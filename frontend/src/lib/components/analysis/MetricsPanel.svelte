<script lang="ts">
	/**
	 * MetricsPanel — aggregate metrics of metric-shaped tools (complexity,
	 * duplication, …), one block per tool: scalar values as figures, count maps
	 * (rank distributions) as a compact row, lists (top functions, clones) as a
	 * short table. Generic over keys; known keys get readable labels.
	 *
	 * Props: `metrics` (tool slug → metrics), `tools` (slug → catalog tool).
	 */
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';

	interface Props {
		metrics: Record<string, Record<string, any>>;
		tools: Map<string, AnalyzerTool>;
	}

	let { metrics, tools }: Props = $props();

	const LABELS: Record<string, string> = {
		duplication_percentage: 'Duplicated lines %',
		token_duplication_percentage: 'Duplicated tokens %',
		duplicated_lines: 'Duplicated lines',
		duplicated_tokens: 'Duplicated tokens',
		// jscpd counts a file once per language in it (JSX + JS + CSS…).
		total_lines: 'Lines (per language)',
		total_sources: 'Sources (per language)',
		clones: 'Clones',
		average_complexity: 'Avg complexity',
		max_complexity: 'Max complexity',
		total_blocks: 'Blocks',
		rank_distribution: 'Complexity ranks',
		top_functions: 'Most complex functions',
		avg_ccn: 'Avg CCN',
		max_ccn: 'Max CCN',
		max_ccn_function: 'Most complex',
		functions: 'Functions',
		total_nloc: 'Lines of code (NLOC)',
		functions_over_15: 'Functions with CCN > 15',
	};

	function label(key: string): string {
		return LABELS[key] ?? key.replaceAll('_', ' ').replace(/^./, (c) => c.toUpperCase());
	}

	function fmt(value: number): string {
		return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(2);
	}

	function isCountMap(v: unknown): v is Record<string, number> {
		return !!v && typeof v === 'object' && !Array.isArray(v) && Object.values(v).every((n) => typeof n === 'number');
	}

	function isRowList(v: unknown): v is Record<string, unknown>[] {
		return Array.isArray(v) && v.length > 0 && v.every((r) => r && typeof r === 'object' && !Array.isArray(r));
	}

	function columns(rows: Record<string, unknown>[]): string[] {
		const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
		// Merge file + line into one location column.
		return keys.filter((k) => k !== 'line' && k !== 'type').slice(0, 5);
	}

	function cell(row: Record<string, unknown>, key: string): string {
		const v = row[key];
		if (key === 'file' && typeof v === 'string') {
			const path = v.replace(/^\/work\//, '');
			return row.line != null ? `${path}:${row.line}` : path;
		}
		if (typeof v === 'number') return fmt(v);
		return v == null ? '—' : String(v);
	}

	const blocks = $derived(
		Object.entries(metrics)
			.filter(([, m]) => m && Object.keys(m).length > 0)
			.map(([slug, m]) => {
				const entries = Object.entries(m);
				return {
					slug,
					scalars: entries.filter(([, v]) => typeof v === 'number' || (typeof v === 'string' && v.length < 80)) as [string, number | string][],
					maps: entries.filter(([, v]) => isCountMap(v)) as [string, Record<string, number>][],
					lists: entries.filter(([, v]) => isRowList(v)) as [string, Record<string, unknown>[]][],
				};
			})
	);
</script>

<div class="grid items-start gap-4 @3xl:grid-cols-2">
	{#each blocks as block (block.slug)}
		{@const tool = tools.get(block.slug)}
		<section class="min-w-0 rounded-md border border-border p-3" aria-label="{tool?.name ?? block.slug} metrics">
			<h3 class="mb-2 flex items-center gap-2 text-sm font-medium">
				<ToolIcon tool={tool ?? { slug: block.slug }} size="xs" />{tool?.name ?? block.slug}
			</h3>
			{#if block.scalars.length > 0}
				<dl class="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
					{#each block.scalars as [key, value] (key)}
						<div class="min-w-0">
							<dt class="truncate text-[11px] text-muted-foreground" title={label(key)}>{label(key)}</dt>
							<dd class="truncate font-mono text-sm font-medium tabular-nums" title={String(value)}>{typeof value === 'number' ? fmt(value) : value}</dd>
						</div>
					{/each}
				</dl>
			{/if}
			{#each block.maps as [key, map] (key)}
				<div class="mt-3">
					<p class="text-[11px] text-muted-foreground">{label(key)}</p>
					<p class="flex flex-wrap gap-x-3 font-mono text-xs tabular-nums">
						{#each Object.entries(map) as [k, n] (k)}<span><span class="text-muted-foreground">{k}</span> {n}</span>{/each}
					</p>
				</div>
			{/each}
			{#each block.lists as [key, rows] (key)}
				{@const cols = columns(rows)}
				<div class="mt-3">
					<p class="mb-1 text-[11px] text-muted-foreground">{label(key)}{rows.length > 5 ? ` (top 5 of ${rows.length})` : ''}</p>
					<div class="overflow-x-auto">
						<table class="text-xs">
							<thead><tr>{#each cols as c (c)}<th class={typeof rows[0][c] === 'number' ? 'num' : ''}>{c.replaceAll('_', ' ')}</th>{/each}</tr></thead>
							<tbody>
								{#each rows.slice(0, 5) as row, i (i)}
									<tr>{#each cols as c (c)}<td class={typeof row[c] === 'number' ? 'num' : 'font-mono'}>{cell(row, c)}</td>{/each}</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</div>
			{/each}
		</section>
	{/each}
</div>
