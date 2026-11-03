<script lang="ts">
	/**
	 * VersionDiff — a line diff with old/new line numbers, plus the structured `[{path, a, b}]` changes.
	 *
	 *   <VersionDiff unified={diff.unified} structured={diff.structured} labels={['v1', 'v2']} />
	 *   <VersionDiff a={baseTemplate} b={draftTemplate} labels={['base', 'draft']} />
	 *
	 * Props: `unified` (server unified diff) or `a`/`b` (texts diffed in the browser), `structured`,
	 * `labels` ([old, new]), `collapse` (fold long unchanged runs; default true), `maxHeight`.
	 */
	import type { DiffEntry } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import { collapseContext, diffStats, lineDiff, parseUnified, structuredRows } from './diff';

	interface Props {
		unified?: string;
		a?: string;
		b?: string;
		structured?: DiffEntry[];
		labels?: [string, string];
		collapse?: boolean;
		maxHeight?: string;
	}

	let { unified, a, b, structured, labels = ['a', 'b'], collapse = true, maxHeight = '36rem' }: Props = $props();

	const rows = $derived.by(() => {
		const raw = unified !== undefined ? parseUnified(unified) : lineDiff(a ?? '', b ?? '');
		return collapse && unified === undefined ? collapseContext(raw) : raw;
	});
	const stats = $derived(diffStats(rows));
	const changes = $derived(structured ? structuredRows(structured) : []);
	const identical = $derived(stats.added === 0 && stats.removed === 0 && changes.length === 0);
</script>

<div class="space-y-3">
	<p class="flex flex-wrap items-center gap-3 font-mono text-[11px] text-muted-foreground">
		<span>{labels[0]} → {labels[1]}</span>
		{#if identical}
			<span>identical</span>
		{:else}
			<span class="text-success-fg">+{stats.added}</span>
			<span class="text-danger-fg">−{stats.removed}</span>
			{#if changes.length}<span>{changes.length} field{changes.length === 1 ? '' : 's'} changed</span>{/if}
		{/if}
	</p>

	{#if changes.length}
		<div class="overflow-x-auto rounded-md border border-border">
			<table class="w-full text-xs">
				<thead>
					<tr><th>Field</th><th>{labels[0]}</th><th>{labels[1]}</th></tr>
				</thead>
				<tbody>
					{#each changes as c (c.path)}
						<tr>
							<td class="font-mono">{c.path}</td>
							<td class={cn('font-mono', c.change !== 'added' && 'text-danger-fg')}>{c.a}</td>
							<td class={cn('font-mono', c.change !== 'removed' && 'text-success-fg')}>{c.b}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if rows.length && !identical}
		<div class="overflow-auto rounded-md border border-border bg-surface-1 font-mono text-xs" style="max-height: {maxHeight}">
			<table class="diff-table w-full border-collapse">
				<tbody>
					{#each rows as row, i (i)}
						<tr
							class={cn(
								row.type === 'add' && 'bg-success/10',
								row.type === 'del' && 'bg-destructive/10',
								(row.type === 'hunk' || row.type === 'meta') && 'bg-muted/50 text-muted-foreground',
							)}
						>
							<td class="w-10 select-none border-r border-border/60 px-1.5 text-right align-top text-muted-foreground tabular-nums">{row.oldNo ?? ''}</td>
							<td class="w-10 select-none border-r border-border/60 px-1.5 text-right align-top text-muted-foreground tabular-nums">{row.newNo ?? ''}</td>
							<td class="w-4 select-none px-1 align-top" aria-hidden="true">{row.type === 'add' ? '+' : row.type === 'del' ? '−' : ''}</td>
							<td class="whitespace-pre-wrap break-all px-1.5 py-px align-top">
								<span class="sr-only">{row.type === 'add' ? 'added: ' : row.type === 'del' ? 'removed: ' : ''}</span>{row.text}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<style>
	/* Diff rows are dense code lines: override the global table cell padding. */
	.diff-table td {
		padding-top: 1px;
		padding-bottom: 1px;
	}
</style>
