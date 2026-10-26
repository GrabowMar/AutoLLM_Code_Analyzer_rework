<script lang="ts">
	/**
	 * CoveragePanel — what the user's generated apps contain, and whether the
	 * installed analyzers measure it. One row per technology found in the apps
	 * (most common first): share of apps, the installed tools that scan it
	 * specifically, and a gap warning when none do. A tool that matches nothing
	 * the models wrote reports "0 findings", which reads as "clean" in model
	 * comparisons — this panel is where that shows up before a study.
	 *
	 * Props:
	 * - `coverage`, `meta`, `catalog`, `installedSlugs` (tools installed and ready).
	 * - `selected` (technology slug or ''), `onselect(slug)`: rows filter the shop table.
	 */
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { cn } from '$lib/utils';
	import type { AnalyzerTool, AppCoverage, CatalogMeta } from '$lib/api/analyzers';

	interface Props {
		coverage: AppCoverage;
		meta: CatalogMeta | null;
		catalog: AnalyzerTool[];
		installedSlugs: Set<string>;
		selected: string;
		onselect: (slug: string) => void;
	}

	let { coverage, meta, catalog, installedSlugs, selected, onselect }: Props = $props();

	const labels = $derived(new Map((meta?.technologies ?? []).map((t) => [t.slug, t.label])));
	const groupOf = $derived(new Map((meta?.technologies ?? []).map((t) => [t.slug, t.group])));

	// "Any source" tools (secret scanners, typo checkers…) apply everywhere and
	// would make every row look covered; rows count technology-specific tools.
	const rows = $derived(
		Object.entries(coverage.technologies)
			.filter(([slug]) => slug !== 'generic')
			.sort((a, b) => b[1] - a[1] || (labels.get(a[0]) ?? a[0]).localeCompare(labels.get(b[0]) ?? b[0]))
			.map(([slug, apps]) => {
				const tools = catalog.filter((t) => t.technologies?.includes(slug));
				const installed = tools.filter((t) => installedSlugs.has(t.slug));
				return {
					slug,
					apps,
					label: labels.get(slug) ?? slug,
					runtime: groupOf.get(slug) === 'runtime',
					installed,
					available: tools.length - installed.length,
					total: tools.length,
				};
			})
	);

	const gaps = $derived(rows.filter((r) => r.installed.length === 0));

	function pct(n: number): number {
		return coverage.apps_total ? Math.round((n / coverage.apps_total) * 100) : 0;
	}

	function names(tools: AnalyzerTool[], max = 3): string {
		const shown = tools.slice(0, max).map((t) => t.name);
		return tools.length > max ? `${shown.join(', ')} +${tools.length - max}` : shown.join(', ');
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border px-4 py-3">
		<Card.Title class="text-sm">What your generated apps contain</Card.Title>
		<Card.Description class="text-xs">
			{#if coverage.apps_total === 0}
				No generated apps yet. Once models have written some, this shows which tools can measure their code.
			{:else}
				Across your {coverage.apps_total} most recent generated app{coverage.apps_total === 1 ? '' : 's'}, and which installed tools measure each part.
				{#if gaps.length > 0}
					<span class="text-warning-fg">{gaps.length} {gaps.length === 1 ? 'technology is' : 'technologies are'} not measured by any installed tool.</span>
				{/if}
				Select a row to list its tools.
			{/if}
		</Card.Description>
	</Card.Header>
	{#if rows.length > 0}
		<!-- Two columns on wide screens; each cell keeps a hairline under it. -->
		<ul class="grid xl:grid-cols-2" aria-label="Technologies in your generated apps">
			{#each rows as row (row.slug)}
				<li class="border-b border-border xl:odd:border-r">
					<button
						type="button"
						class={cn(
							'grid h-full w-full cursor-pointer grid-cols-[minmax(7rem,9rem)_1fr] items-center gap-x-4 gap-y-1 px-4 py-2 text-left text-xs transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40 sm:grid-cols-[9rem_10rem_1fr]',
							selected === row.slug && 'bg-primary/5'
						)}
						aria-pressed={selected === row.slug}
						onclick={() => onselect(selected === row.slug ? '' : row.slug)}
					>
						<span class="font-medium text-foreground">{row.label}</span>
						<span class="flex items-center gap-2" title="{row.apps} of {coverage.apps_total} apps">
							<span class="h-1.5 w-14 overflow-hidden rounded-full bg-muted" aria-hidden="true">
								<span class="block h-full rounded-full bg-primary/70" style:width="{pct(row.apps)}%"></span>
							</span>
							<span class="whitespace-nowrap tabular-nums text-muted-foreground">{row.apps}/{coverage.apps_total} apps</span>
						</span>
						<span class="col-span-2 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 sm:col-span-1">
							{#if row.installed.length > 0}
								<StatusBadge status="measured" tone="success" label="{row.installed.length} installed" />
								<span class="truncate text-muted-foreground">{names(row.installed)}</span>
							{:else if row.total > 0}
								<StatusBadge status="gap" tone="warning" label="Not measured" />
								<span class="text-muted-foreground">{row.available} tool{row.available === 1 ? '' : 's'} in the shop</span>
							{:else}
								<StatusBadge status="gap" tone="warning" label="No tool" />
								<span class="text-muted-foreground">Only language-agnostic tools read it</span>
							{/if}
							{#if row.runtime && row.installed.length > 0}
								<span class="text-muted-foreground">· needs the app running</span>
							{/if}
						</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</Card.Root>
