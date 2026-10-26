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
import Zap from '@lucide/svelte/icons/zap';
import { Checkbox } from '$lib/components/ui/checkbox';
import { SortableTh, TableToolbar, rowLink, tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';
import ProviderIcon from '$lib/components/ProviderIcon.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import type { Snippet } from 'svelte';
import type { LLMModelSummary, PaginatedModels } from '$lib/api/client';

type SortableColumn = {
	key: string;
	label: string;
	sortField: string;
	align?: 'left' | 'right';
	help?: string;
};

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
}: Props = $props();

const someVisibleSelected = $derived(
	!allVisibleSelected && _visibleModelSlugs.some((slug) => selectedModelSlugs.has(slug)),
);

const sortableColumns: SortableColumn[] = [
	{ key: 'model', label: 'Model', sortField: 'model_name' },
	{ key: 'provider', label: 'Provider', sortField: 'provider' },
	{ key: 'context', label: 'Context', sortField: 'context_window', align: 'right', help: 'Context window (tokens)' },
	{ key: 'max_output', label: 'Max out', sortField: 'max_output', align: 'right', help: 'Maximum output tokens per response' },
	{ key: 'input_price', label: 'In $/1M', sortField: 'input_price', align: 'right', help: 'Input price per million tokens' },
	{ key: 'output_price', label: 'Out $/1M', sortField: 'output_price', align: 'right', help: 'Output price per million tokens' },
	{ key: 'efficiency', label: 'Value', sortField: 'cost_efficiency', align: 'right', help: 'Cost-efficiency grade: capability per dollar (A+ best, D worst)' },
];

const capIconMap: Record<string, { icon: typeof Eye; label: string }> = {
	'Vision': { icon: Eye, label: 'Vision' },
	'Function Calling': { icon: Wrench, label: 'Function Calling' },
	'JSON Mode': { icon: Braces, label: 'JSON Mode' },
	'Streaming': { icon: Radio, label: 'Streaming' },
	'Code': { icon: Zap, label: 'Code' },
};

function efficiencyGrade(score: number): { grade: string; color: string; bg: string } {
	const success = { color: 'text-success-fg', bg: 'bg-success/10' };
	const info = { color: 'text-info-fg', bg: 'bg-info/10' };
	const warning = { color: 'text-warning-fg', bg: 'bg-warning/10' };
	if (score >= 0.9) return { grade: 'A+', ...success };
	if (score >= 0.8) return { grade: 'A', ...success };
	if (score >= 0.7) return { grade: 'A-', ...success };
	if (score >= 0.6) return { grade: 'B+', ...info };
	if (score >= 0.5) return { grade: 'B', ...info };
	if (score >= 0.4) return { grade: 'B-', ...info };
	if (score >= 0.3) return { grade: 'C+', ...warning };
	if (score >= 0.2) return { grade: 'C', ...warning };
	return { grade: 'D', color: 'text-danger-fg', bg: 'bg-destructive/10' };
}

function formatTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
	if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
	return String(n);
}
</script>

<!-- Reloads keep the current rows (dimmed) so paging never collapses the page. -->
<Card.Root class="gap-0 overflow-clip py-0">
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
						{data.items.length} on this page · click a row to open, tick to compare
					{/snippet}
					{#snippet bulk()}
						<Button size="sm" class="h-7 px-2.5 text-xs" onclick={onCompare}>
							<GitCompareArrows class="size-3.5" />Compare
						</Button>
					{/snippet}
				</TableToolbar>
				<div class="table-frame" {@attach tableFrame}>
					<table>
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
								{#each sortableColumns as col (col.key)}
									<SortableTh label={col.label} field={col.sortField} {sortBy} {sortDir} onSort={onToggleSort} align={col.align} help={col.help} />
								{/each}
								<th class="text-right">Capabilities</th>
							</tr>
						</thead>
						<tbody>
							{#each data.items as model (model.canonical_slug)}
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
									<td class="max-w-0 w-[42%]">
										<div class="flex items-center gap-2.5">
											<span class="flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted/50">
												<ProviderIcon provider={model.provider} size={16} />
											</span>
											<div class="min-w-0">
												<div class="flex items-center gap-1.5">
													<a {href} class="truncate font-medium hover:text-primary" title={model.model_name}>{shortModelName(model.model_name)}</a>
													{#if model.is_free}
														<span class="shrink-0 rounded-sm bg-success/12 px-1.5 py-px text-[11px] font-medium leading-4 text-success-fg">Free</span>
													{/if}
												</div>
												{#if model.description}
													<p class="cell-sub truncate text-[11px] text-muted-foreground" title={model.description}>{model.description}</p>
												{/if}
											</div>
										</div>
									</td>
									<td class="whitespace-nowrap text-muted-foreground">{modelNamePrefix(model.model_name) || model.provider}</td>
									<td class="num">{model.context_window_display}</td>
									<td class="num text-muted-foreground">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—'}</td>
									<td class="num">
										{#if model.input_price_per_million === 0}<span class="text-success-fg">Free</span>{:else}{formatPrice(model.input_price_per_million)}{/if}
									</td>
									<td class="num">
										{#if model.output_price_per_million === 0}<span class="text-success-fg">Free</span>{:else}{formatPrice(model.output_price_per_million)}{/if}
									</td>
									<td class="num">
										{#if model.cost_efficiency > 0}
											{@const eff = efficiencyGrade(model.cost_efficiency)}
											<span
												class="inline-flex h-5 min-w-7 items-center justify-center rounded-sm px-1 font-mono text-[11px] font-semibold {eff.color} {eff.bg}"
												title="Cost efficiency {(model.cost_efficiency * 100).toFixed(0)}/100"
											>{eff.grade}</span>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
									</td>
									<td>
										<div class="flex items-center justify-end gap-1">
											{#each model.capabilities as cap (cap)}
												{#if capIconMap[cap]}
													{@const ci = capIconMap[cap]}
													<span title={ci.label} class="inline-flex size-6 items-center justify-center rounded border border-border/70 text-muted-foreground">
														<ci.icon class="size-3.5" aria-hidden="true" />
														<span class="sr-only">{ci.label}</span>
													</span>
												{/if}
											{/each}
											{#if model.capabilities.length === 0}
												<span class="text-muted-foreground">—</span>
											{/if}
										</div>
									</td>
								</tr>
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
										{#if model.is_free}<span class="shrink-0 rounded-sm bg-success/12 px-1.5 text-[11px] font-medium leading-4 text-success-fg">Free</span>{/if}
										{#if model.cost_efficiency > 0}
											{@const eff = efficiencyGrade(model.cost_efficiency)}
											<span class="ml-auto inline-flex h-5 min-w-7 shrink-0 items-center justify-center rounded-sm px-1 font-mono text-[11px] font-semibold {eff.color} {eff.bg}" title="Cost-efficiency grade">{eff.grade}</span>
										{/if}
									</div>
									<p class="mt-0.5 truncate text-[11px] text-muted-foreground">{modelNamePrefix(model.model_name) || model.provider}</p>
									<dl class="mt-2 grid grid-cols-4 gap-2 text-[11px]">
										<div><dt class="text-muted-foreground">Context</dt><dd class="font-mono text-xs tabular-nums">{model.context_window_display}</dd></div>
										<div><dt class="text-muted-foreground">Max out</dt><dd class="font-mono text-xs tabular-nums">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—'}</dd></div>
										<div><dt class="text-muted-foreground">In $/1M</dt><dd class="font-mono text-xs tabular-nums {model.input_price_per_million === 0 ? 'text-success-fg' : ''}">{model.input_price_per_million === 0 ? 'Free' : formatPrice(model.input_price_per_million)}</dd></div>
										<div><dt class="text-muted-foreground">Out $/1M</dt><dd class="font-mono text-xs tabular-nums {model.output_price_per_million === 0 ? 'text-success-fg' : ''}">{model.output_price_per_million === 0 ? 'Free' : formatPrice(model.output_price_per_million)}</dd></div>
									</dl>
									{#if model.capabilities.some((c) => capIconMap[c])}
										<div class="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
											{#each model.capabilities as cap (cap)}
												{#if capIconMap[cap]}
													{@const ci = capIconMap[cap]}
													<span class="inline-flex items-center gap-1"><ci.icon class="size-3" aria-hidden="true" />{ci.label}</span>
												{/if}
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
