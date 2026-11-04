<script lang="ts">
	/**
	 * SlotFileTree — the left pane of the slot picker (spec 10.5 step 2): the frozen parent's file tree (origin
	 * badges, symbol counts; tests and excluded files dimmed) plus tabs listing the indexed Endpoints (method,
	 * route, confidence — heuristic routes are marked — and linked check / probe counts) and Components.
	 * Choosing an endpoint or a component selects that part; choosing a file opens it in the code pane.
	 *
	 * Props: `picker` (`SlotPicker`).
	 */
	import { Segmented } from '$lib/components/ui/segmented';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { cn } from '$lib/utils';
	import type { RunFileOut } from '$lib/api/v3/types';
	import FileTree from '$lib/components/runs/FileTree.svelte';
	import { buildFileTree } from '$lib/components/runs/format';
	import type { SlotPicker } from './picker.svelte';
	import { selectionForEndpoint, symbolPath } from './selection';

	interface Props {
		picker: SlotPicker;
	}

	let { picker }: Props = $props();

	let pane = $state<'files' | 'endpoints' | 'components'>('files');
	let query = $state('');

	const asFiles = $derived(
		picker.tree.map(
			(t): RunFileOut => ({
				path: t.path,
				origin: t.origin,
				size: 0,
				loc: t.loc ?? 0,
				language: t.language,
				is_test: t.is_test,
				excluded: t.excluded,
				blob: 'x',
			}),
		),
	);
	const symbolCounts = $derived(new Map(picker.tree.map((t) => [t.path, t.symbols])));
	const filteredFiles = $derived(
		query ? asFiles.filter((f) => f.path.toLowerCase().includes(query.toLowerCase())) : asFiles,
	);
	const nodes = $derived(buildFileTree(filteredFiles));
	const unparsed = $derived(new Set(picker.set?.index_stats?.unparsed ?? []));

	function extra(file: RunFileOut): string {
		if (unparsed.has(file.path)) return 'unparsed';
		const n = symbolCounts.get(file.path) ?? 0;
		return n ? `${n} sym` : '';
	}

	const selectedEndpoint = $derived(picker.selection?.kind === 'endpoint' ? picker.selection.symbol_id : null);
	const selectedComponent = $derived(picker.selection?.kind === 'component' ? picker.selection.component : null);
</script>

<div class="flex h-full min-h-0 flex-col gap-2">
	<Segmented
		size="sm"
		fullWidth
		aria-label="Browse the parent by"
		bind:value={pane}
		items={[
			{ value: 'files', label: 'Files', badge: String(picker.tree.length) },
			{ value: 'endpoints', label: 'Endpoints', badge: String(picker.endpoints.length) },
			{ value: 'components', label: 'Components', badge: String(picker.components.length) },
		]}
	/>

	{#if pane === 'files'}
		<Input class="h-8" placeholder="Filter paths" aria-label="Filter paths" bind:value={query} />
		<div class="min-h-0 flex-1 overflow-auto">
			<FileTree {nodes} selected={picker.path} onselect={(path) => picker.openFile(path)} {extra} />
		</div>
	{:else if pane === 'endpoints'}
		<ul class="min-h-0 flex-1 space-y-1 overflow-auto text-xs">
			{#each picker.endpoints as endpoint (endpoint.handler + endpoint.method + endpoint.path)}
				{@const file = symbolPath(endpoint.handler)}
				<li>
					<button
						type="button"
						class={cn(
							'w-full cursor-pointer rounded border px-2 py-1.5 text-left transition-colors hover:border-primary/60',
							selectedEndpoint === endpoint.handler ? 'border-primary bg-primary/10' : 'border-border',
						)}
						onclick={() => picker.select(selectionForEndpoint(endpoint, file))}
					>
						<span class="font-mono font-medium">{endpoint.method}</span>
						<span class="font-mono">{endpoint.path}</span>
						<div class="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
							<span class="truncate font-mono">{endpoint.handler.split('::').pop()}</span>
							{#if endpoint.confidence !== 'exact'}
								<Badge variant="secondary" title="The route could not be resolved exactly; check it before using it">{endpoint.confidence}</Badge>
							{/if}
							{#if endpoint.spec_endpoint_id}
								<span title="Matched task endpoint">→ {endpoint.spec_endpoint_id}</span>
							{:else}
								<span title="Not matched to a task endpoint: no linked checks">unmatched</span>
							{/if}
							<span class="ml-auto tabular-nums" title="Linked checks · probes">{endpoint.linked_checks} checks · {endpoint.linked_probes} probes</span>
						</div>
					</button>
				</li>
			{:else}
				<li class="text-muted-foreground">No routes were indexed in this parent.</li>
			{/each}
		</ul>
	{:else}
		<ul class="min-h-0 flex-1 space-y-1 overflow-auto text-xs">
			{#each picker.components as component (component.path + component.name)}
				<li>
					<button
						type="button"
						class={cn(
							'w-full cursor-pointer rounded border px-2 py-1.5 text-left transition-colors hover:border-primary/60',
							selectedComponent === component.name ? 'border-primary bg-primary/10' : 'border-border',
						)}
						onclick={() =>
							picker.select({ kind: 'component', path: component.path, component: component.name, symbol_id: component.symbol })}
					>
						<span class="font-medium">{component.name}</span>
						<span class="text-muted-foreground"> · {component.framework}{component.whole_file ? ' · whole file' : ''}</span>
						<div class="truncate font-mono text-[11px] text-muted-foreground">{component.path}</div>
					</button>
				</li>
			{:else}
				<li class="text-muted-foreground">No UI components were indexed in this parent.</li>
			{/each}
		</ul>
	{/if}
</div>
