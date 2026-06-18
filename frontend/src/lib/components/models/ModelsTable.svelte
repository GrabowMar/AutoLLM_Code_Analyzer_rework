<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import Cpu from '@lucide/svelte/icons/cpu';
import Search from '@lucide/svelte/icons/search';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import ArrowUpDown from '@lucide/svelte/icons/arrow-up-down';
import ArrowUp from '@lucide/svelte/icons/arrow-up';
import ArrowDown from '@lucide/svelte/icons/arrow-down';
import Eye from '@lucide/svelte/icons/eye';
import Wrench from '@lucide/svelte/icons/wrench';
import Radio from '@lucide/svelte/icons/radio';
import Braces from '@lucide/svelte/icons/braces';
import Zap from '@lucide/svelte/icons/zap';
import ProviderIcon from '$lib/components/ProviderIcon.svelte';
import type { LLMModelSummary, PaginatedModels } from '$lib/api/client';

type SortableColumn = {
	key: string;
	label: string;
	sortField: string;
	align?: 'left' | 'right';
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
}: Props = $props();

const sortableColumns: SortableColumn[] = [
	{ key: 'model', label: 'Model', sortField: 'model_name' },
	{ key: 'provider', label: 'Provider', sortField: 'provider' },
	{ key: 'context', label: 'Context', sortField: 'context_window', align: 'right' },
	{ key: 'max_output', label: 'Max Out', sortField: 'max_output', align: 'right' },
	{ key: 'input_price', label: 'In $/1M', sortField: 'input_price', align: 'right' },
	{ key: 'output_price', label: 'Out $/1M', sortField: 'output_price', align: 'right' },
	{ key: 'efficiency', label: 'Efficiency', sortField: 'cost_efficiency', align: 'right' },
];

const capIconMap: Record<string, { icon: typeof Eye; color: string; label: string }> = {
	'Vision': { icon: Eye, color: 'text-violet-500', label: 'Vision' },
	'Function Calling': { icon: Wrench, color: 'text-orange-500', label: 'Function Calling' },
	'JSON Mode': { icon: Braces, color: 'text-blue-500', label: 'JSON Mode' },
	'Streaming': { icon: Radio, color: 'text-emerald-500', label: 'Streaming' },
	'Code': { icon: Zap, color: 'text-slate-500', label: 'Code' },
};

function formatPrice(price: number): string {
	if (price === 0) return 'Free';
	if (price < 0) return '—';
	if (price < 0.01) return `$${price.toFixed(4)}`;
	return `$${price.toFixed(2)}`;
}

function efficiencyGrade(score: number): { grade: string; color: string; bg: string } {
	if (score >= 0.9) return { grade: 'A+', color: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-500/10' };
	if (score >= 0.8) return { grade: 'A', color: 'text-emerald-600 dark:text-emerald-400', bg: 'bg-emerald-500/10' };
	if (score >= 0.7) return { grade: 'A-', color: 'text-emerald-500', bg: 'bg-emerald-500/10' };
	if (score >= 0.6) return { grade: 'B+', color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-500/10' };
	if (score >= 0.5) return { grade: 'B', color: 'text-blue-600 dark:text-blue-400', bg: 'bg-blue-500/10' };
	if (score >= 0.4) return { grade: 'B-', color: 'text-blue-500', bg: 'bg-blue-500/10' };
	if (score >= 0.3) return { grade: 'C+', color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-500/10' };
	if (score >= 0.2) return { grade: 'C', color: 'text-amber-600 dark:text-amber-400', bg: 'bg-amber-500/10' };
	return { grade: 'D', color: 'text-red-500', bg: 'bg-red-500/10' };
}

function formatTokens(n: number): string {
	if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
	if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
	return String(n);
}
</script>

<Card.Root>
	<Card.Content class="p-0">
		{#if loading}
			<div class="flex items-center justify-center py-20">
				<LoaderCircle class="h-8 w-8 animate-spin text-muted-foreground" />
			</div>
		{:else if data && data.items.length > 0}
			<div class="hidden md:block overflow-x-auto">
				<table class="w-full">
					<thead>
						<tr class="border-b bg-muted/40 sticky top-0 z-10">
							<th class="px-3 py-2.5 text-center text-xs font-medium text-muted-foreground whitespace-nowrap w-12">
								<input
									type="checkbox"
									checked={allVisibleSelected}
									onchange={onToggleVisibleSelection}
									aria-label="Select visible models"
								/>
							</th>
							{#each sortableColumns as col}
								<th
									class="px-3 py-2.5 text-{col.align ?? 'left'} text-xs font-medium text-muted-foreground whitespace-nowrap"
									aria-sort={sortBy === col.sortField ? (sortDir === 'asc' ? 'ascending' : 'descending') : 'none'}
								>
									<button
										class="inline-flex items-center gap-1 hover:text-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring rounded"
										onclick={() => onToggleSort(col.sortField)}
									>
										{col.label}
										{#if sortBy === col.sortField}
											{#if sortDir === 'asc'}
												<ArrowUp class="h-3 w-3 text-primary" />
											{:else}
												<ArrowDown class="h-3 w-3 text-primary" />
											{/if}
										{:else}
											<ArrowUpDown class="h-3 w-3 opacity-30" />
										{/if}
									</button>
								</th>
							{/each}
							<th class="px-3 py-2.5 text-center text-xs font-medium text-muted-foreground">Capabilities</th>
						</tr>
					</thead>
					<tbody>
						{#each data.items as model, i (model.canonical_slug)}
							<tr class="border-b transition-colors hover:bg-muted/50 group
								{i % 2 === 0 ? '' : 'bg-muted/15'}
								{model.is_free ? 'bg-emerald-500/[0.03]' : ''}">
								<td class="px-3 py-2 text-center align-top">
									<input
										type="checkbox"
										checked={selectedModelSlugs.has(model.canonical_slug)}
										onchange={() => onToggleModelSelection(model.canonical_slug)}
										aria-label={`Select ${model.model_name}`}
									/>
								</td>
								<td class="px-3 py-2">
									<a href="/models/{model.canonical_slug}" class="flex items-center gap-2.5 group/link">
										<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted group-hover/link:bg-primary/10 transition-colors">
											<ProviderIcon provider={model.provider} size={20} />
										</div>
										<div class="min-w-0">
											<span class="text-sm font-medium group-hover/link:text-primary transition-colors block truncate max-w-[280px]">{model.model_name}</span>
											{#if model.description}
												<span class="text-[11px] text-muted-foreground block truncate max-w-[280px]">{model.description}</span>
											{:else if model.is_free}
												<span class="text-[10px] text-emerald-600 dark:text-emerald-400 font-medium">FREE</span>
											{/if}
										</div>
									</a>
								</td>
								<td class="px-3 py-2">
									<Badge variant="outline" class="text-[10px] font-normal">{model.provider}</Badge>
								</td>
								<td class="px-3 py-2 text-right">
									<span class="text-sm font-mono tabular-nums">{model.context_window_display}</span>
								</td>
								<td class="px-3 py-2 text-right">
									<span class="text-sm font-mono tabular-nums text-muted-foreground">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—'}</span>
								</td>
								<td class="px-3 py-2 text-right">
									<span class="text-sm font-mono tabular-nums {model.input_price_per_million === 0 ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}">{formatPrice(model.input_price_per_million)}</span>
								</td>
								<td class="px-3 py-2 text-right">
									<span class="text-sm font-mono tabular-nums {model.output_price_per_million === 0 ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}">{formatPrice(model.output_price_per_million)}</span>
								</td>
								<td class="px-3 py-2 text-right">
									{#if model.cost_efficiency > 0}
										{@const eff = efficiencyGrade(model.cost_efficiency)}
										<span class="inline-flex items-center justify-center h-6 w-8 rounded text-[10px] font-bold {eff.color} {eff.bg}">{eff.grade}</span>
									{:else}
										<span class="text-xs text-muted-foreground">—</span>
									{/if}
								</td>
								<td class="px-3 py-2">
									<div class="flex items-center justify-center gap-1">
										{#each model.capabilities as cap}
											{#if capIconMap[cap]}
												{@const ci = capIconMap[cap]}
												<span title={ci.label} class="inline-flex items-center justify-center h-5 w-5 rounded {ci.color} bg-muted/60">
													<ci.icon class="h-3 w-3" />
												</span>
											{/if}
										{/each}
										{#if model.capabilities.length === 0}
											<span class="text-xs text-muted-foreground">—</span>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<!-- Mobile card view -->
			<div class="md:hidden">
				<div class="flex items-center gap-2 border-b px-3 py-2.5">
					<span class="text-xs font-medium text-muted-foreground shrink-0">Sort:</span>
					<select
						class="h-8 flex-1 rounded-md border border-input bg-background px-2 text-xs ring-offset-background focus:outline-none focus:ring-1 focus:ring-ring"
						bind:value={sortBy}
						onchange={onChangeMobileSort}
					>
						<option value="">Default</option>
						{#each sortableColumns as col}
							<option value={col.sortField}>{col.label}</option>
						{/each}
					</select>
					<button
						class="inline-flex h-8 w-8 items-center justify-center rounded-md border border-input bg-background transition-colors hover:bg-muted"
						onclick={onToggleSortDir}
						aria-label="Toggle sort direction"
					>
						{#if sortDir === 'asc'}
							<ArrowUp class="h-3.5 w-3.5" />
						{:else}
							<ArrowDown class="h-3.5 w-3.5" />
						{/if}
					</button>
				</div>
				<div class="space-y-3 p-3">
					{#each data.items as model (model.canonical_slug)}
						<a href="/models/{model.canonical_slug}" class="block rounded-lg border bg-card p-3 transition-colors hover:bg-muted/50 active:bg-muted/70 {model.is_free ? 'border-emerald-500/30' : ''}">
							<div class="flex items-start justify-between gap-2">
								<div class="flex items-center gap-2 min-w-0 flex-1">
									<div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted">
										<ProviderIcon provider={model.provider} size={20} />
									</div>
									<div class="min-w-0">
										<span class="text-sm font-medium block truncate">{model.model_name}</span>
										{#if model.description}
											<span class="text-[11px] text-muted-foreground block truncate">{model.description}</span>
										{/if}
									</div>
								</div>
								<div class="flex items-center gap-1.5 shrink-0">
									{#if model.is_free}
										<Badge variant="secondary" class="text-[10px] text-emerald-600 dark:text-emerald-400">FREE</Badge>
									{/if}
									<Badge variant="outline" class="text-[10px] font-normal">{model.provider}</Badge>
								</div>
							</div>
							<div class="grid grid-cols-2 gap-x-4 gap-y-1.5 mt-2.5 text-xs">
								<div class="flex justify-between">
									<span class="text-muted-foreground">Context</span>
									<span class="font-mono tabular-nums">{model.context_window_display}</span>
								</div>
								<div class="flex justify-between">
									<span class="text-muted-foreground">Max Out</span>
									<span class="font-mono tabular-nums text-muted-foreground">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—'}</span>
								</div>
								<div class="flex justify-between">
									<span class="text-muted-foreground">In $/1M</span>
									<span class="font-mono tabular-nums {model.input_price_per_million === 0 ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}">{formatPrice(model.input_price_per_million)}</span>
								</div>
								<div class="flex justify-between">
									<span class="text-muted-foreground">Out $/1M</span>
									<span class="font-mono tabular-nums {model.output_price_per_million === 0 ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}">{formatPrice(model.output_price_per_million)}</span>
								</div>
							</div>
							<div class="flex items-center justify-between mt-2.5 pt-2 border-t border-border/50">
								<div class="flex flex-wrap gap-1">
									{#each model.capabilities as cap}
										{#if capIconMap[cap]}
											{@const ci = capIconMap[cap]}
											<span class="inline-flex items-center gap-0.5 rounded px-1.5 py-0.5 text-[10px] {ci.color} bg-muted/60">
												<ci.icon class="h-2.5 w-2.5" />
												{ci.label}
											</span>
										{/if}
									{/each}
									{#if model.capabilities.length === 0}
										<span class="text-[10px] text-muted-foreground">—</span>
									{/if}
								</div>
								{#if model.cost_efficiency > 0}
									{@const eff = efficiencyGrade(model.cost_efficiency)}
									<span class="inline-flex items-center justify-center h-6 w-8 rounded text-[10px] font-bold {eff.color} {eff.bg}">{eff.grade}</span>
								{/if}
							</div>
						</a>
					{/each}
				</div>
			</div>
		{:else}
			<div class="flex flex-col items-center justify-center py-20 gap-3">
				{#if hasActiveFilters}
					<Search class="h-10 w-10 text-muted-foreground/40" />
					<p class="text-sm text-muted-foreground">No models match your filters.</p>
					<Button variant="outline" size="sm" onclick={onClearAllFilters}>Clear all filters</Button>
				{:else}
					<Cpu class="h-10 w-10 text-muted-foreground/40" />
					<p class="text-sm text-muted-foreground">No models found. Click "Sync from OpenRouter" to load models.</p>
				{/if}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
