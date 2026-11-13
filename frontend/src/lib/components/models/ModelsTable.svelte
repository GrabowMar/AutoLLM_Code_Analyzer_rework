<script lang="ts">
import { formatPrice } from '$lib/utils/formatters';
import * as Card from '$lib/components/ui/card';
import * as Select from '$lib/components/ui/select';
import { Button } from '$lib/components/ui/button';
import Cpu from '@lucide/svelte/icons/cpu';
import Search from '@lucide/svelte/icons/search';
import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
import ArrowUp from '@lucide/svelte/icons/arrow-up';
import ArrowDown from '@lucide/svelte/icons/arrow-down';
import Eye from '@lucide/svelte/icons/eye';
import Wrench from '@lucide/svelte/icons/wrench';
import Radio from '@lucide/svelte/icons/radio';
import Braces from '@lucide/svelte/icons/braces';
import Brain from '@lucide/svelte/icons/brain';
import ImageIcon from '@lucide/svelte/icons/image';
import FileText from '@lucide/svelte/icons/file-text';
import AudioLines from '@lucide/svelte/icons/audio-lines';
import Video from '@lucide/svelte/icons/video';
import { Checkbox } from '$lib/components/ui/checkbox';
import { SortableTh, TableToolbar, rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';
import ProviderIcon from '$lib/components/ProviderIcon.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import type { Snippet } from 'svelte';
import type { LLMModelSummary, PaginatedModels } from '$lib/api/client';
import { knownTokenLimit } from './helpers';
import { onMount } from 'svelte';
import ColumnsMenu from './ColumnsMenu.svelte';
import { COLUMNS, columnPrefs, type ColumnDef } from './columns.svelte';

interface Props {
	data: PaginatedModels | null;
	loading: boolean;
	sortBy: string;
	sortDir: 'asc' | 'desc';
	selectedModelSlugs: Set<string>;
	visibleModelSlugs: string[];
	allVisibleSelected: boolean;
	hasActiveFilters: boolean;
	onToggleSort: (field: string) => void;
	onToggleModelSelection: (slug: string) => void;
	onToggleVisibleSelection: () => void;
	onChangeMobileSort: () => void;
	onToggleSortDir: () => void;
	onClearAllFilters: () => void;
	onClearSelection: () => void;
	onCompare: () => void;
	/** Card footer under the rows (pagination). */
	footer?: Snippet;
	/** Top strip of the card (search + filters), rendered in every state. */
	toolbar?: Snippet;
}

let {
	data,
	loading,
	sortBy = $bindable(),
	sortDir,
	selectedModelSlugs,
	visibleModelSlugs: _visibleModelSlugs,
	allVisibleSelected,
	hasActiveFilters,
	onToggleSort,
	onToggleModelSelection,
	onToggleVisibleSelection,
	onChangeMobileSort,
	onToggleSortDir,
	onClearAllFilters,
	onClearSelection,
	onCompare,
	footer,
	toolbar,
}: Props = $props();

const someVisibleSelected = $derived(
	!allVisibleSelected && _visibleModelSlugs.some((slug) => selectedModelSlugs.has(slug)),
);

// The viewer's column choice (Columns menu), restored from localStorage after mount.
onMount(() => columnPrefs.restore());
const visibleColumns = $derived(columnPrefs.visible);

/** Mobile sort select: the name plus every sortable column. */
const sortableColumns = [
	{ label: 'Model', sortField: 'model_name' },
	...COLUMNS.filter((c): c is ColumnDef & { sortField: string } => !!c.sortField),
];

// "Code" is on every model (hardcoded server-side), so it is left out: it carries no information.
const CAPS: { key: keyof LLMModelSummary; icon: typeof Eye; label: string }[] = [
	{ key: 'supports_reasoning', icon: Brain, label: 'Reasoning' },
	{ key: 'supports_function_calling', icon: Wrench, label: 'Tool calling' },
	{ key: 'supports_vision', icon: Eye, label: 'Vision' },
	{ key: 'supports_json_mode', icon: Braces, label: 'JSON mode' },
	{ key: 'supports_streaming', icon: Radio, label: 'Streaming' },
];
const capsOf = (m: LLMModelSummary) => CAPS.filter((c) => m[c.key] === true);
const shownCaps = $derived(CAPS.filter((c) => columnPrefs.capabilities.includes(c.key as never)));

// Input types and research data use fixed slots (like capabilities), so each reads as its own stripe.
const MODALITIES = [
	{ key: 'image', label: 'Images', icon: ImageIcon },
	{ key: 'file', label: 'Files / PDFs', icon: FileText },
	{ key: 'audio', label: 'Audio', icon: AudioLines },
	{ key: 'video', label: 'Video', icon: Video },
] as const;
const DATA_SOURCES = [
	{ key: 'lmarena', short: 'LMA', title: 'LMArena scores' },
	{ key: 'epoch', short: 'EP', title: 'Epoch AI (compute, parameters)' },
	{ key: 'huggingface', short: 'HF', title: 'Hugging Face model card' },
] as const;

function formatParams(m: LLMModelSummary): { text: string; title: string } | null {
	const n = m.parameters;
	if (n && n > 0) {
		const text = n >= 1e12 ? `${(n / 1e12).toFixed(1)}T` : n >= 1e9 ? `${(n / 1e9).toFixed(n >= 1e10 ? 0 : 1)}B` : `${(n / 1e6).toFixed(0)}M`;
		return m.parameters_source === 'epoch'
			? { text: `≈${text}`, title: `${n.toLocaleString()} parameters, as reported by Epoch AI` }
			: { text, title: `${n.toLocaleString()} parameters, counted from the Hugging Face weights` };
	}
	if (m.parameter_size) return { text: m.parameter_size, title: 'Size label reported by the local endpoint' };
	return null;
}

function formatReleased(iso: string): string {
	const d = new Date(`${iso}T00:00:00Z`);
	return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' });
}

/** Local endpoint models have no per-token price; "Free" would read as a catalog price. */
const isLocal = (m: LLMModelSummary) => !!m.is_local;
const localTitle = (m: LLMModelSummary) =>
	`Served by the local endpoint ${m.endpoint?.name ?? m.provider}: no per-token price`;

/** Provider heading a row sits under: the endpoint for local models, the catalog provider otherwise. */
const groupOf = (m: LLMModelSummary) =>
	isLocal(m) ? (m.endpoint?.name ?? m.provider) : modelNamePrefix(m.model_name) || m.provider;

// Default order is provider → name, so rows group under provider headings; any other sort is a flat list.
const grouped = $derived(!sortBy || sortBy === 'provider');
const rows = $derived.by(() => {
	const out: ({ kind: 'group'; label: string; provider: string; count: number } | { kind: 'model'; model: LLMModelSummary })[] = [];
	let current: { kind: 'group'; label: string; provider: string; count: number } | null = null;
	for (const m of data?.items ?? []) {
		const g = groupOf(m);
		if (grouped && (!current || current.label !== g)) {
			current = { kind: 'group', label: g, provider: m.provider, count: 0 };
			out.push(current);
		}
		if (current) current.count++;
		out.push({ kind: 'model', model: m });
	}
	return out;
});

/** Context window on a log scale, 4K → 2M, for the inline bar. */
function ctxPct(n: number): number {
	if (!n || n <= 0) return 0;
	const lo = Math.log10(4096), hi = Math.log10(2_000_000);
	return Math.max(0.04, Math.min(1, (Math.log10(n) - lo) / (hi - lo))) * 100;
}

function formatTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
	if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
	return String(n);
}
</script>

{#snippet dash(title = 'Not reported by the source')}<span class="text-muted-foreground" {title}>—</span>{/snippet}

{#snippet cell(col: ColumnDef, model: LLMModelSummary)}
	{#if col.key === 'provider'}
		<td class="whitespace-nowrap text-muted-foreground">{groupOf(model)}</td>
	{:else if col.key === 'model_id'}
		<td class="max-w-[16rem] truncate font-mono text-[11px] text-muted-foreground" title={model.model_id}>{model.model_id}</td>
	{:else if col.key === 'description'}
		<td class="max-w-[22rem] truncate text-xs text-muted-foreground" title={model.description}>{model.description || '—'}</td>
	{:else if col.key === 'released'}
		<td class="num whitespace-nowrap text-muted-foreground" title={model.released_at ?? undefined}>{#if model.released_at}{formatReleased(model.released_at)}{:else}{@render dash()}{/if}</td>
	{:else if col.key === 'context'}
		<td class="num">
			{#if knownTokenLimit(model.context_window) != null}
				<span class="bar-cell">
					<span class="bar" aria-hidden="true"><span style:width="{ctxPct(model.context_window)}%"></span></span>
					<span class="bar-val">{model.context_window_display}</span>
				</span>
			{:else}{@render dash()}{/if}
		</td>
	{:else if col.key === 'max_out'}
		<td class="num text-muted-foreground">{#if knownTokenLimit(model.max_output_tokens) != null}{formatTokens(model.max_output_tokens)}{:else}{@render dash()}{/if}</td>
	{:else if col.key === 'input' || col.key === 'output'}
		{@const price = col.key === 'input' ? model.input_price_per_million : model.output_price_per_million}
		<td class="num {col.key === 'output' ? 'text-muted-foreground' : ''}">
			{#if isLocal(model)}{@render dash(localTitle(model))}{:else if price === 0}<span class="text-success-fg">Free</span>{:else}{formatPrice(price)}{/if}
		</td>
	{:else if col.key === 'ctx_per_dollar'}
		<td class="num" title="Context per dollar {(model.cost_efficiency * 100).toFixed(0)}/100 (cost lens, not quality)">
			{#if !isLocal(model) && model.cost_efficiency > 0}
				<span class="bar-cell">
					<span class="bar bar-neutral" aria-hidden="true"><span style:width="{Math.max(4, model.cost_efficiency * 100)}%"></span></span>
					<span class="bar-val">{(model.cost_efficiency * 100).toFixed(0)}</span>
				</span>
			{:else}{@render dash(isLocal(model) ? localTitle(model) : 'No price data')}{/if}
		</td>
	{:else if col.key === 'params'}
		{@const pm = formatParams(model)}
		<td class="num" title={pm?.title}>{#if pm}{pm.text}{:else}{@render dash()}{/if}</td>
	{:else if col.key === 'weights'}
		<td class="whitespace-nowrap text-xs">
			{#if model.open_weights == null}{@render dash('Not covered by models.dev')}
			{:else}
				<span class="weights" data-open={model.open_weights}>
					<span class="weights-dot" aria-hidden="true"></span>{model.open_weights ? 'Open' : 'Closed'}
				</span>
			{/if}
		</td>
	{:else if col.key === 'tokenizer'}
		<td class="whitespace-nowrap text-xs {model.tokenizer && model.tokenizer !== 'Other' ? '' : 'text-faint'}">{model.tokenizer || '—'}</td>
	{:else if col.key === 'modalities'}
		{@const mods = model.input_modalities ?? []}
		<td title={mods.length ? `Accepts: ${mods.join(', ')}` : 'Not reported'}>
			<span class="caps-row slots-left">
				{#each MODALITIES as m (m.key)}
					{#if mods.includes(m.key)}
						<span class="cap-on" title={m.label}><m.icon class="size-3.5" aria-hidden="true" /><span class="sr-only">{m.label}</span></span>
					{:else}<span class="cap-off" aria-hidden="true"></span>{/if}
				{/each}
			</span>
		</td>
	{:else if col.key === 'research'}
		{@const have = model.research_data ?? []}
		<td>
			<span class="data-row">
				{#each DATA_SOURCES as d (d.key)}
					{#if have.includes(d.key)}<span class="data-on" title={d.title}>{d.short}</span>
					{:else}<span class="data-off" aria-hidden="true"></span>{/if}
				{/each}
			</span>
		</td>
	{:else if col.key === 'capabilities'}
		<td class="text-right">
			<!-- Fixed slots: each capability keeps its own column, so they read as vertical stripes. -->
			<span class="caps-row">
				{#each shownCaps as c (c.key)}
					{#if model[c.key] === true}
						<span class="cap-on" title={c.label}><c.icon class="size-3.5" aria-hidden="true" /><span class="sr-only">{c.label}</span></span>
					{:else}
						<span class="cap-off" aria-hidden="true"></span>
					{/if}
				{/each}
			</span>
		</td>
	{/if}
{/snippet}

<!-- Reloads keep the current rows (dimmed) so paging never collapses the page. -->
<Card.Root class="gap-0 overflow-clip py-0">
	{@render toolbar?.()}
	<Card.Content class="p-0 transition-opacity duration-150 aria-busy:opacity-60" aria-busy={loading && !!data}>
		{#if loading && !data}
			<LoadingState label="loading models" />
		{:else if data && data.items.length > 0}
			<div class="hidden md:block">
				<TableToolbar
					selectedCount={selectedModelSlugs.size}
					noun="model"
					onClearSelection={onClearSelection}
				>
					{#snippet summary()}
						{data.items.length} on this page · tick to compare
					{/snippet}
					{#snippet bulk()}
						<Button size="sm" class="h-7 px-2.5 text-xs" onclick={onCompare}>
							<GitCompareArrows class="size-3.5" />Compare
						</Button>
					{/snippet}
					{#snippet controls()}<ColumnsMenu />{/snippet}
				</TableToolbar>
				<div class="table-frame" {@attach tableFrame}>
					<table class="models-table">
						<thead>
							<tr>
								<th class="w-10">
									<Checkbox
										checked={allVisibleSelected}
										indeterminate={someVisibleSelected}
										onCheckedChange={onToggleVisibleSelection}
										aria-label="Select all models on this page"
									/>
								</th>
								<SortableTh label="Model" field="model_name" {sortBy} {sortDir} onSort={onToggleSort} />
								{#each visibleColumns as col (col.key)}
									{#if col.key === 'capabilities'}
										<th class="text-right">
											<span class="caps-head" aria-label="Capabilities">
												{#each shownCaps as c (c.key)}<span title={c.label}><c.icon class="size-3" aria-hidden="true" /></span>{/each}
											</span>
										</th>
									{:else if col.key === 'modalities'}
										<th title={col.help}>
											<span class="caps-head slots-left" aria-label="Input types besides text">
												{#each MODALITIES as m (m.key)}<span title={m.label}><m.icon class="size-3" aria-hidden="true" /></span>{/each}
											</span>
										</th>
									{:else if col.key === 'research'}
										<th title={col.help}>
											<span class="data-head" aria-label="Research data">
												{#each DATA_SOURCES as d (d.key)}<span title={d.title}>{d.short}</span>{/each}
											</span>
										</th>
									{:else if col.sortField}
										<SortableTh label={col.label} field={col.sortField} {sortBy} {sortDir} onSort={onToggleSort} align={col.align} help={col.help} />
									{:else}
										<th class={col.align === 'right' ? 'text-right' : ''} title={col.help}>{col.label}</th>
									{/if}
								{/each}
							</tr>
						</thead>
						<tbody>
							{#each rows as row, i (row.kind === 'group' ? `g:${row.label}:${i}` : row.model.canonical_slug)}
								{#if row.kind === 'group'}
									<tr class="group-row">
										<th colspan={visibleColumns.length + 2} scope="colgroup">
											<span class="flex items-center gap-2">
												<ProviderIcon provider={row.provider} size={14} />
												<span class="group-name">{row.label}</span>
												<span class="group-count">{row.count}</span>
											</span>
										</th>
									</tr>
								{:else}
									{@const model = row.model}
									{@const selected = selectedModelSlugs.has(model.canonical_slug)}
									{@const href = `/models/${model.canonical_slug}`}
									<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
									<tr class="row-link group" data-selected={selected} onclick={rowLink(href)}>
										<td>
											<Checkbox
												checked={selected}
												onCheckedChange={() => onToggleModelSelection(model.canonical_slug)}
												aria-label={`Select ${model.model_name}`}
											/>
										</td>
										<td class="model-cell max-w-0">
											<div class="flex min-w-0 items-center gap-2">
												{#if !grouped}<ProviderIcon provider={model.provider} size={14} />{/if}
												<a {href} class="truncate font-medium hover:text-primary" title={model.description || model.model_name}>{shortModelName(model.model_name)}</a>
												{#if isLocal(model)}
													<span class="tag tag-local" title={localTitle(model)}>Local</span>
												{:else if model.is_free}
													<span class="tag tag-free">Free</span>
												{/if}
												{#if !grouped && !columnPrefs.columns.includes('provider')}<span class="truncate text-xs text-muted-foreground">{groupOf(model)}</span>{/if}
											</div>
										</td>
										{#each visibleColumns as col (col.key)}
											{@render cell(col, model)}
										{/each}
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			</div>
			<!-- Mobile list -->
			<div class="md:hidden">
				<div class="flex items-center gap-2 border-b border-border px-3 py-2">
					<Select.Simple
						size="sm"
						class="flex-1"
						aria-label="Sort models"
						bind:value={sortBy}
						onValueChange={onChangeMobileSort}
						items={[{ value: '', label: 'Default order' }, ...sortableColumns.map((c) => ({ value: c.sortField, label: `Sort: ${c.label}` }))]}
					/>
					<Button variant="outline" size="sm" class="size-9 p-0" onclick={onToggleSortDir} aria-label={sortDir === 'asc' ? 'Ascending — switch to descending' : 'Descending — switch to ascending'}>
						{#if sortDir === 'asc'}<ArrowUp class="size-4" />{:else}<ArrowDown class="size-4" />{/if}
					</Button>
				</div>
				{#if selectedModelSlugs.size > 0}
					<div class="flex items-center justify-between gap-2 border-b border-border bg-primary/5 px-3 py-2 text-xs" aria-live="polite">
						<span class="font-medium">{selectedModelSlugs.size} selected</span>
						<div class="flex items-center gap-1">
							<Button variant="ghost" size="sm" class="h-8 px-2 text-xs" onclick={onClearSelection}>Clear</Button>
							<Button size="sm" class="h-8 px-2.5 text-xs" onclick={onCompare}><GitCompareArrows class="size-3.5" />Compare</Button>
						</div>
					</div>
				{/if}
				<ul>
					{#each data.items as model (model.canonical_slug)}
						{@const selected = selectedModelSlugs.has(model.canonical_slug)}
						<li class="data-list-row px-3" data-active={selected}>
							<div class="flex items-start gap-3">
								<Checkbox
									class="mt-2"
									checked={selected}
									onCheckedChange={() => onToggleModelSelection(model.canonical_slug)}
									aria-label={`Select ${model.model_name} to compare`}
								/>
								<a href="/models/{model.canonical_slug}" class="min-w-0 flex-1">
									<div class="flex items-center gap-2">
										<ProviderIcon provider={model.provider} size={16} />
										<span class="truncate text-sm font-medium">{shortModelName(model.model_name)}</span>
										{#if isLocal(model)}<span class="shrink-0 rounded-sm bg-info/12 px-1.5 text-[11px] font-medium leading-4 text-info-fg">Local</span>
										{:else if model.is_free}<span class="shrink-0 rounded-sm bg-success/12 px-1.5 text-[11px] font-medium leading-4 text-success-fg">Free</span>{/if}
									</div>
									<p class="mt-0.5 truncate text-[11px] text-muted-foreground">{isLocal(model) ? (model.endpoint?.name ?? model.provider) : modelNamePrefix(model.model_name) || model.provider}</p>
									<dl class="mt-2 grid grid-cols-4 gap-2 text-[11px]">
										<div><dt class="text-muted-foreground">Context</dt><dd class="font-mono text-xs tabular-nums">{#if knownTokenLimit(model.context_window) != null}{model.context_window_display}{:else}<span class="text-muted-foreground">—</span>{/if}</dd></div>
										<div><dt class="text-muted-foreground">Max out</dt><dd class="font-mono text-xs tabular-nums">{#if knownTokenLimit(model.max_output_tokens) != null}{formatTokens(model.max_output_tokens)}{:else}<span class="text-muted-foreground">—</span>{/if}</dd></div>
										{#if isLocal(model)}
											<div><dt class="text-muted-foreground">In $/1M</dt><dd class="font-mono text-xs text-muted-foreground">—</dd></div>
											<div><dt class="text-muted-foreground">Out $/1M</dt><dd class="font-mono text-xs text-muted-foreground">—</dd></div>
										{:else}
											<div><dt class="text-muted-foreground">In $/1M</dt><dd class="font-mono text-xs tabular-nums {model.input_price_per_million === 0 ? 'text-success-fg' : ''}">{model.input_price_per_million === 0 ? 'Free' : formatPrice(model.input_price_per_million)}</dd></div>
											<div><dt class="text-muted-foreground">Out $/1M</dt><dd class="font-mono text-xs tabular-nums {model.output_price_per_million === 0 ? 'text-success-fg' : ''}">{model.output_price_per_million === 0 ? 'Free' : formatPrice(model.output_price_per_million)}</dd></div>
										{/if}
									</dl>
									{#if capsOf(model).length}
										<div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
											{#each capsOf(model) as ci (ci.key)}
												<span class="inline-flex items-center gap-1"><ci.icon class="size-3" aria-hidden="true" />{ci.label}</span>
											{/each}
										</div>
									{/if}
								</a>
							</div>
						</li>
					{/each}
				</ul>
			</div>
			{@render footer?.()}
		{:else}
			{#if hasActiveFilters}
				<EmptyState icon={Search} title="No models match your filters" description="Loosen or clear the filters to see more models.">
					{#snippet action()}
						<Button variant="outline" size="sm" onclick={onClearAllFilters}>Clear all filters</Button>
					{/snippet}
				</EmptyState>
			{:else}
				<EmptyState icon={Cpu} title="No models yet" description="Use Sync to load models from OpenRouter." />
			{/if}
		{/if}
	</Card.Content>
</Card.Root>

<style>
	.models-table :global(td) {
		vertical-align: middle;
	}
	/* Provider heading rows */
	.group-row th {
		padding-block: 0.5rem 0.375rem;
		background: color-mix(in oklab, var(--muted) 45%, var(--card));
		border-top: 1px solid var(--border);
		text-align: left;
		font-weight: 400;
		text-transform: none;
		letter-spacing: normal;
	}
	.group-name {
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--foreground);
	}
	.group-count {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		color: var(--muted-foreground);
		font-variant-numeric: tabular-nums;
	}

	/* Free / Local tags */
	.tag {
		flex-shrink: 0;
		border-radius: 0.1875rem;
		padding: 0 0.375rem;
		font-size: 0.6875rem;
		font-weight: 500;
		line-height: 1.125rem;
	}
	.tag-free {
		color: var(--success-fg);
		background: color-mix(in oklab, var(--success) 12%, transparent);
	}
	/* Weights: same shape for both values — a dot and a word. */
	.weights {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		color: var(--muted-foreground);
	}
	.weights[data-open='true'] {
		color: var(--foreground);
	}
	.weights-dot {
		width: 0.4375rem;
		height: 0.4375rem;
		border-radius: 9999px;
		border: 1.5px solid currentColor;
		opacity: 0.6;
	}
	.weights[data-open='true'] .weights-dot {
		border-color: var(--chart-2, #0d9488);
		background: var(--chart-2, #0d9488);
		opacity: 1;
	}
	.text-faint {
		color: color-mix(in oklab, var(--muted-foreground) 55%, transparent);
	}
	.slots-left {
		justify-content: flex-start;
	}
	/* Research data: three fixed mono slots (LMA · EP · HF). */
	.data-row,
	.data-head {
		display: inline-flex;
		align-items: center;
		gap: 0.25rem;
	}
	.data-row > span,
	.data-head > span {
		display: inline-flex;
		width: 1.75rem;
		height: 1.125rem;
		align-items: center;
		justify-content: center;
		font-family: var(--font-mono);
		font-size: 0.625rem;
		letter-spacing: 0.04em;
	}
	.data-head > span {
		color: var(--muted-foreground);
	}
	.data-on {
		border-radius: 0.1875rem;
		line-height: 1.125rem;
		color: var(--accent-foreground);
		background: var(--accent);
	}
	.data-off::before {
		content: '';
		width: 3px;
		height: 3px;
		border-radius: 9999px;
		background: var(--border);
	}
	.model-cell {
		width: 30%;
		min-width: 17rem;
	}
	.tag-local {
		color: var(--info-fg);
		background: color-mix(in oklab, var(--info) 12%, transparent);
	}

	/* Scale columns (context, ctx/$): a short bar, then the value — one line, aligned with the row. */
	.bar-cell {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.5rem;
	}
	.bar {
		display: block;
		flex-shrink: 0;
		width: 2.5rem;
		height: 3px;
		border-radius: 9999px;
		background: var(--muted);
		overflow: hidden;
	}
	.bar > span {
		display: block;
		height: 100%;
		border-radius: inherit;
		background: color-mix(in oklab, var(--primary) 70%, var(--muted-foreground));
	}
	.bar-neutral > span {
		background: color-mix(in oklab, var(--muted-foreground) 60%, transparent);
	}
	.bar-val {
		min-width: 2.25rem;
		text-align: right;
	}

	/* Capability slots */
	.caps-row,
	.caps-head {
		display: inline-flex;
		align-items: center;
		justify-content: flex-end;
		gap: 0.375rem;
	}
	.caps-row > span,
	.caps-head > span {
		display: inline-flex;
		width: 1rem;
		height: 1rem;
		justify-content: center;
		align-items: center;
	}
	.caps-head {
		color: var(--muted-foreground);
	}
	.cap-on {
		color: var(--foreground);
		opacity: 0.8;
	}
	.cap-off::before {
		content: '';
		width: 3px;
		height: 3px;
		border-radius: 9999px;
		background: var(--border);
	}
</style>
