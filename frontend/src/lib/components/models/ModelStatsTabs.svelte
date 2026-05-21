<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Separator } from '$lib/components/ui/separator';
	import Info from '@lucide/svelte/icons/info';
	import Zap from '@lucide/svelte/icons/zap';
	import DollarSign from '@lucide/svelte/icons/dollar-sign';
	import Layers from '@lucide/svelte/icons/layers';
	import Users from '@lucide/svelte/icons/users';
	import Globe from '@lucide/svelte/icons/globe';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Box from '@lucide/svelte/icons/box';
	import Settings from '@lucide/svelte/icons/settings';
	import Gauge from '@lucide/svelte/icons/gauge';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import GraduationCap from '@lucide/svelte/icons/graduation-cap';
	import ArrowDownToLine from '@lucide/svelte/icons/arrow-down-to-line';
	import ArrowUpFromLine from '@lucide/svelte/icons/arrow-up-from-line';
	import Gift from '@lucide/svelte/icons/gift';
	import Shield from '@lucide/svelte/icons/shield';
	import Code from '@lucide/svelte/icons/code';
	import Copy from '@lucide/svelte/icons/copy';
	import Download from '@lucide/svelte/icons/download';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Activity from '@lucide/svelte/icons/activity';
	import { onMount } from 'svelte';
	import type { LLMModelDetail } from '$lib/api/client';
	import ModelApplicationsSection from './ModelApplicationsSection.svelte';
	import ModelUsageSection from './ModelUsageSection.svelte';
	import {
		formatPrice,
		formatTokens,
		formatNumber,
		formatMetaValue,
		stripMarkdown,
		getCoreSkills,
		getAllPricingTiers,
		copyToClipboard,
		exportJson,
	} from './helpers';

	interface Props {
		model: LLMModelDetail;
		meta: Record<string, unknown>;
		caps: Record<string, unknown>;
		arch: Record<string, unknown>;
		efficiency: { grade: string; color: string };
		inputMods: string[];
		outputMods: string[];
		supportedParams: string[];
		perRequestLimits: Record<string, number>;
		capMatrix: Record<string, boolean>;
		instructType: string;
		defaultParams: Record<string, unknown>;
		hfId: string | null;
		slug: string;
		onApplicationsLoaded?: (n: number) => void;
	}

	let {
		model,
		meta,
		caps,
		arch,
		efficiency,
		inputMods,
		outputMods,
		supportedParams,
		perRequestLimits,
		capMatrix,
		instructType,
		defaultParams,
		hfId,
		slug,
		onApplicationsLoaded,
	}: Props = $props();

	let calcInputTokens = $state(1000);
	let calcOutputTokens = $state(1000);
	let capMatrixExpanded = $state(false);
	let rawJsonOpen = $state(false);

	const calcInputCost = $derived((calcInputTokens / 1000) * model.input_price_per_million);
	const calcOutputCost = $derived((calcOutputTokens / 1000) * model.output_price_per_million);
	const calcTotalCost = $derived(calcInputCost + calcOutputCost);

	const sections = [
		{ id: 'overview', label: 'Overview', icon: Info },
		{ id: 'capabilities', label: 'Capabilities', icon: Zap },
		{ id: 'pricing', label: 'Pricing', icon: DollarSign },
		{ id: 'applications', label: 'Applications', icon: AppWindow },
		{ id: 'usage', label: 'Usage', icon: Activity },
		{ id: 'metadata', label: 'Metadata', icon: Layers },
		{ id: 'related', label: 'Related', icon: Users },
	] as const;
	let activeSection = $state<string>('overview');

	function scrollToSection(id: string) {
		activeSection = id;
		document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	onMount(() => {
		const observer = new IntersectionObserver(
			(entries) => {
				for (const entry of entries) {
					if (entry.isIntersecting) {
						activeSection = entry.target.id;
					}
				}
			},
			{ rootMargin: '-20% 0px -70% 0px' },
		);
		for (const s of sections) {
			const el = document.getElementById(s.id);
			if (el) observer.observe(el);
		}
		return () => observer.disconnect();
	});
</script>

<!-- Sticky Section Nav -->
<div class="sticky top-0 z-20 -mx-1 px-1 py-2 bg-background/95 backdrop-blur border-b">
	<div class="flex items-center gap-1 overflow-x-auto">
		{#each sections as section}
			<button
				class="flex items-center gap-1.5 px-3 py-2.5 sm:py-1.5 rounded-md text-xs font-medium transition-colors whitespace-nowrap {activeSection === section.id ? 'bg-primary/10 text-primary border border-primary/30' : 'text-muted-foreground hover:text-foreground hover:bg-muted/50'}"
				onclick={() => scrollToSection(section.id)}
			>
				<section.icon class="h-3.5 w-3.5" />
				{section.label}
			</button>
		{/each}
	</div>
</div>

<!-- ==================== OVERVIEW SECTION ==================== -->
<section id="overview" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Info class="h-5 w-5" /> Overview</h2>

	<div class="grid gap-3 lg:grid-cols-2">
		<!-- Identity Card -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Identity</Card.Title></Card.Header>
			<Card.Content class="space-y-2 text-sm">
				<div class="flex justify-between"><span class="text-muted-foreground">Model ID</span><code class="text-xs truncate max-w-[180px]" title={model.model_id}>{model.model_id}</code></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Provider</span><Badge variant="outline" class="text-xs">{model.provider}</Badge></div>
				<div class="flex justify-between"><span class="text-muted-foreground">Last Updated</span><span class="text-xs">{new Date(model.updated_at).toLocaleDateString()}</span></div>
				{#if hfId}
					<div class="flex justify-between"><span class="text-muted-foreground">HuggingFace</span><code class="text-xs truncate max-w-[140px]">{hfId}</code></div>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Capacity Card -->
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Capacity</Card.Title></Card.Header>
			<Card.Content>
				<div class="flex items-center justify-around mb-3">
					<div class="text-center">
						<div class="text-2xl font-bold text-primary">{model.context_window_display}</div>
						<div class="text-[10px] text-muted-foreground uppercase">Context</div>
					</div>
					<div class="text-center">
						<div class="text-2xl font-bold text-emerald-500">{model.max_output_tokens ? formatTokens(model.max_output_tokens) : '—'}</div>
						<div class="text-[10px] text-muted-foreground uppercase">Max Output</div>
					</div>
				</div>
				<div class="flex flex-wrap gap-1 justify-center">
					{#if model.is_free}
						<Badge variant="secondary" class="gap-1 text-emerald-600 dark:text-emerald-400"><Gift class="h-3 w-3" /> Free</Badge>
					{/if}
					{#if (meta.openrouter_top_provider as Record<string, unknown>)?.is_moderated}
						<Badge variant="secondary" class="gap-1 text-blue-600 dark:text-blue-400"><Shield class="h-3 w-3" /> Moderated</Badge>
					{/if}
				</div>
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Description & Links -->
	<Card.Root>
		<Card.Content class="p-4">
			<div class="flex flex-wrap items-center gap-3">
				<a href="https://openrouter.ai/models/{model.model_id}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 text-sm text-primary hover:underline font-medium">
					<Globe class="h-3.5 w-3.5" />
					View on OpenRouter
					<ExternalLink class="h-3 w-3" />
				</a>
				{#if hfId}
					<Separator orientation="vertical" class="h-4" />
					<a href="https://huggingface.co/{hfId}" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1.5 text-sm text-primary hover:underline">
						<span>🤗</span>
						<code class="text-xs">{hfId}</code>
						<ExternalLink class="h-3 w-3" />
					</a>
				{/if}
			</div>
			{#if model.description}
				<p class="text-sm text-muted-foreground leading-relaxed mt-2 break-words">
					{stripMarkdown(model.description)}
				</p>
			{:else}
				<p class="text-sm text-muted-foreground leading-relaxed mt-2 break-words">
					{model.model_name} by {model.provider}. A large language model available via OpenRouter.
				</p>
			{/if}
		</Card.Content>
	</Card.Root>
</section>

<!-- ==================== CAPABILITIES SECTION ==================== -->
<section id="capabilities" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Zap class="h-5 w-5" /> Capabilities</h2>

	<Card.Root>
		<Card.Content class="space-y-4 pt-4">
			<!-- Core Skills -->
			<div>
				<div class="flex items-center gap-2 mb-2">
					<Sparkles class="h-3.5 w-3.5 text-amber-500" />
					<span class="text-xs font-bold uppercase text-muted-foreground">Skills</span>
				</div>
				<div class="flex flex-wrap gap-1.5">
					{#each getCoreSkills(model) as skill}
						<Badge variant={skill.active ? 'default' : 'outline'} class="{skill.active ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20' : 'opacity-50'} gap-1">
							<skill.icon class="h-3 w-3" />
							{skill.name}
						</Badge>
					{/each}
				</div>
			</div>

			<Separator />

			<div class="grid gap-3 md:grid-cols-2">
				<!-- Architecture -->
				<div>
					<div class="flex items-center gap-2 mb-2">
						<Box class="h-3.5 w-3.5 text-primary" />
						<span class="text-xs font-bold uppercase text-muted-foreground">Architecture</span>
					</div>
					<div class="grid grid-cols-2 gap-2 text-sm mb-2">
						<div>
							<span class="text-xs text-muted-foreground">Modality:</span>
							<span class="ml-1 font-medium">{arch.modality || meta.architecture_modality || '—'}</span>
						</div>
						<div>
							<span class="text-xs text-muted-foreground">Tokenizer:</span>
							<span class="ml-1 font-medium">{arch.tokenizer || meta.architecture_tokenizer || '—'}</span>
						</div>
					</div>
					{#if inputMods.length || outputMods.length}
						<div class="flex flex-wrap gap-1">
							{#each inputMods as mod}
								<Badge variant="outline" class="text-xs gap-1"><ArrowDownToLine class="h-2.5 w-2.5" />{mod}</Badge>
							{/each}
							{#each outputMods as mod}
								<Badge variant="outline" class="text-xs gap-1"><ArrowUpFromLine class="h-2.5 w-2.5" />{mod}</Badge>
							{/each}
						</div>
					{/if}
				</div>

				<!-- Supported Parameters -->
				<div>
					<div class="flex items-center gap-2 mb-2">
						<Settings class="h-3.5 w-3.5 text-blue-500" />
						<span class="text-xs font-bold uppercase text-muted-foreground">Supported Parameters</span>
					</div>
					{#if supportedParams.length}
						<div class="flex flex-wrap gap-1">
							{#each supportedParams.slice(0, 8) as param}
								<Badge variant="outline" class="text-xs">{param}</Badge>
							{/each}
							{#if supportedParams.length > 8}
								<Badge variant="outline" class="text-xs text-muted-foreground">+{supportedParams.length - 8} more</Badge>
							{/if}
						</div>
					{:else}
						<span class="text-xs text-muted-foreground">Not published</span>
					{/if}
				</div>
			</div>

			<Separator />

			<div class="grid gap-3 md:grid-cols-3">
				<!-- Per-Request Limits -->
				<div>
					<div class="flex items-center gap-2 mb-2">
						<Gauge class="h-3.5 w-3.5 text-red-500" />
						<span class="text-xs font-bold uppercase text-muted-foreground">Per-Request Limits</span>
					</div>
					{#if Object.keys(perRequestLimits).length}
						<div class="space-y-1 text-sm">
							{#if perRequestLimits.prompt_tokens}
								<div class="flex justify-between">
									<span class="text-xs text-muted-foreground">Prompt:</span>
									<Badge variant="outline" class="text-xs">{formatNumber(perRequestLimits.prompt_tokens)}</Badge>
								</div>
							{/if}
							{#if perRequestLimits.completion_tokens}
								<div class="flex justify-between">
									<span class="text-xs text-muted-foreground">Completion:</span>
									<Badge variant="outline" class="text-xs">{formatNumber(perRequestLimits.completion_tokens)}</Badge>
								</div>
							{/if}
						</div>
					{:else}
						<span class="text-xs text-muted-foreground">Not specified</span>
					{/if}
				</div>

				<!-- Capability Matrix -->
				{#if Object.keys(capMatrix).length}
					{@const sortedEntries = Object.entries(capMatrix).sort(([a], [b]) => a.localeCompare(b))}
					{@const visibleEntries = capMatrixExpanded ? sortedEntries : sortedEntries.slice(0, 8)}
					<div class="md:col-span-2">
						<div class="flex items-center gap-2 mb-2">
							<CircleCheck class="h-3.5 w-3.5 text-emerald-500" />
							<span class="text-xs font-bold uppercase text-muted-foreground">Capability Matrix</span>
						</div>
						<div class="flex flex-wrap gap-1">
							{#each visibleEntries as [key, value]}
								<Badge variant="outline" class="{value ? 'text-emerald-600 dark:text-emerald-400 border-emerald-500/30' : 'opacity-40'} text-xs gap-1">
									{#if value}<Check class="h-2.5 w-2.5" />{:else}<X class="h-2.5 w-2.5" />{/if}
									{key.replace(/_/g, ' ')}
								</Badge>
							{/each}
							{#if !capMatrixExpanded && sortedEntries.length > 8}
								<button class="text-xs text-primary hover:underline ml-1" onclick={() => capMatrixExpanded = true}>
									Show {sortedEntries.length - 8} more
								</button>
							{:else if capMatrixExpanded && sortedEntries.length > 8}
								<button class="text-xs text-primary hover:underline ml-1" onclick={() => capMatrixExpanded = false}>
									Show less
								</button>
							{/if}
						</div>
					</div>
				{/if}
			</div>

			<!-- Instruct Type & Default Parameters -->
			{#if instructType || Object.keys(defaultParams).length}
				<Separator />
				<div class="grid gap-3 md:grid-cols-2">
					{#if instructType}
						<div>
							<div class="flex items-center gap-2 mb-2">
								<GraduationCap class="h-3.5 w-3.5 text-purple-500" />
								<span class="text-xs font-bold uppercase text-muted-foreground">Instruct Type</span>
							</div>
							<Badge variant="secondary">{instructType}</Badge>
						</div>
					{/if}
					{#if Object.keys(defaultParams).length}
						<div>
							<div class="flex items-center gap-2 mb-2">
								<Settings class="h-3.5 w-3.5 text-purple-500" />
								<span class="text-xs font-bold uppercase text-muted-foreground">Default Parameters</span>
							</div>
							<div class="space-y-1 text-sm">
								{#each Object.entries(defaultParams) as [key, value]}
									<div class="flex justify-between">
										<span class="text-xs text-muted-foreground">{key}</span>
										<code class="text-xs">{String(value)}</code>
									</div>
								{/each}
							</div>
						</div>
					{/if}
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</section>

<!-- ==================== PRICING SECTION ==================== -->
<section id="pricing" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><DollarSign class="h-5 w-5" /> Pricing</h2>
	<!-- Pricing Stat Cards -->
	<div class="grid grid-cols-2 md:grid-cols-4 gap-3">
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Input</Card.Title></Card.Header>
			<Card.Content class="space-y-1">
				<div class="text-xl font-bold">{formatPrice(model.input_price_per_million)}</div>
				<div class="text-xs text-muted-foreground">per 1M tokens</div>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Output</Card.Title></Card.Header>
			<Card.Content class="space-y-1">
				<div class="text-xl font-bold">{formatPrice(model.output_price_per_million)}</div>
				<div class="text-xs text-muted-foreground">per 1M tokens</div>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Efficiency</Card.Title></Card.Header>
			<Card.Content class="space-y-1">
				<div class="text-xl font-bold {efficiency.color}">{efficiency.grade}</div>
				<div class="text-xs text-muted-foreground">score {model.cost_efficiency.toFixed(2)}</div>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Tier</Card.Title></Card.Header>
			<Card.Content class="space-y-1">
				<div class="text-xl font-bold {model.is_free ? 'text-emerald-500' : ''}">{model.is_free ? 'Free' : 'Paid'}</div>
				<div class="text-xs text-muted-foreground">{model.is_free ? 'No cost' : 'Usage-based'}</div>
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Pricing Table -->
	<Card.Root>
		<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Pricing Breakdown</Card.Title></Card.Header>
		<Card.Content class="p-0">
			<div class="table-scroll-wrapper">
			<table class="w-full">
				<thead>
					<tr class="border-b bg-muted/30">
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Type</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Per 1M tokens</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Per 1K tokens</th>
						<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Per token</th>
					</tr>
				</thead>
				<tbody class="divide-y">
					<tr class="hover:bg-muted/30">
						<td class="px-4 py-2.5 text-sm font-medium">Input</td>
						<td class="px-4 py-2.5 text-sm font-mono">{formatPrice(model.input_price_per_million)}</td>
						<td class="px-4 py-2.5 text-sm font-mono text-muted-foreground">{formatPrice(model.input_price_per_million / 1000)}</td>
						<td class="px-4 py-2.5 text-sm font-mono text-muted-foreground">{model.input_price_per_token === 0 ? 'Free' : `$${model.input_price_per_token.toExponential(2)}`}</td>
					</tr>
					<tr class="hover:bg-muted/30">
						<td class="px-4 py-2.5 text-sm font-medium">Output</td>
						<td class="px-4 py-2.5 text-sm font-mono">{formatPrice(model.output_price_per_million)}</td>
						<td class="px-4 py-2.5 text-sm font-mono text-muted-foreground">{formatPrice(model.output_price_per_million / 1000)}</td>
						<td class="px-4 py-2.5 text-sm font-mono text-muted-foreground">{model.output_price_per_token === 0 ? 'Free' : `$${model.output_price_per_token.toExponential(2)}`}</td>
					</tr>
					{#each getAllPricingTiers(model) as tier}
						<tr class="hover:bg-muted/30 bg-muted/10">
							<td class="px-4 py-2.5 text-sm font-medium text-muted-foreground">{tier.label}</td>
							<td class="px-4 py-2.5 text-sm font-mono">{formatPrice(tier.perMillionTokens)}</td>
							<td class="px-4 py-2.5 text-sm font-mono text-muted-foreground">{formatPrice(tier.perMillionTokens / 1000)}</td>
							<td class="px-4 py-2.5 text-sm font-mono text-muted-foreground">${(tier.perMillionTokens / 1_000_000).toExponential(2)}</td>
						</tr>
					{/each}
				</tbody>
			</table>
			</div>
		</Card.Content>
	</Card.Root>

	<!-- Cost Calculator -->
	<Card.Root>
		<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Cost Calculator</Card.Title></Card.Header>
		<Card.Content>
			<div class="grid gap-4 grid-cols-1 sm:grid-cols-[1fr_1fr_auto]">
				<div>
					<label for="calc-input-tokens" class="text-xs font-medium text-muted-foreground block mb-1.5">Input tokens (K)</label>
					<input id="calc-input-tokens" type="number" min="0" step="100" bind:value={calcInputTokens} class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm tabular-nums" />
					<div class="text-xs text-muted-foreground mt-1">${calcInputCost.toFixed(4)}</div>
				</div>
				<div>
					<label for="calc-output-tokens" class="text-xs font-medium text-muted-foreground block mb-1.5">Output tokens (K)</label>
					<input id="calc-output-tokens" type="number" min="0" step="100" bind:value={calcOutputTokens} class="h-9 w-full rounded-md border border-input bg-background px-3 text-sm tabular-nums" />
					<div class="text-xs text-muted-foreground mt-1">${calcOutputCost.toFixed(4)}</div>
				</div>
				<div class="flex flex-col justify-center items-center rounded-lg bg-muted/30 px-6 py-3 min-w-[140px]">
					<div class="text-xs text-muted-foreground mb-1">Estimated Cost</div>
					<div class="text-2xl font-bold tabular-nums {calcTotalCost === 0 ? 'text-emerald-500' : 'text-foreground'}">{calcTotalCost === 0 ? 'Free' : `$${calcTotalCost.toFixed(4)}`}</div>
					<div class="text-[10px] text-muted-foreground mt-0.5">{calcInputTokens}K in + {calcOutputTokens}K out</div>
				</div>
			</div>
			<div class="flex flex-wrap gap-2 mt-3">
				{#each [{ label: '1K', v: 1000 }, { label: '10K', v: 10000 }, { label: '100K', v: 100000 }, { label: '1M', v: 1000000 }] as preset}
					<button class="rounded border border-input px-2 py-0.5 text-xs hover:bg-muted transition-colors" onclick={() => { calcInputTokens = preset.v; calcOutputTokens = Math.round(preset.v * 0.5); }}>
						{preset.label} tokens
					</button>
				{/each}
			</div>
		</Card.Content>
	</Card.Root>
</section>

<!-- ==================== APPLICATIONS SECTION ==================== -->
<ModelApplicationsSection modelId={model.model_id} {slug} onLoaded={onApplicationsLoaded} />

<!-- ==================== USAGE SECTION ==================== -->
<ModelUsageSection modelId={model.model_id} />

<!-- ==================== METADATA SECTION ==================== -->
<section id="metadata" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Layers class="h-5 w-5" /> Metadata</h2>

	<!-- Quick Reference Bar -->
	<Card.Root>
		<Card.Content class="p-3">
			<div class="flex flex-wrap items-center gap-2 sm:gap-3 text-sm">
				<div class="flex items-center gap-1">
					<span class="text-xs text-muted-foreground">ID:</span>
					<code class="text-xs truncate max-w-[180px] sm:max-w-none">{model.model_id}</code>
				</div>
				<Separator orientation="vertical" class="h-4 hidden sm:block" />
				<div class="hidden sm:flex items-center gap-1">
					<span class="text-xs text-muted-foreground">DB:</span>
					<span class="text-xs font-bold">#{model.id}</span>
				</div>
				<Separator orientation="vertical" class="h-4 hidden sm:block" />
				<div class="hidden sm:flex items-center gap-1">
					<span class="text-xs text-muted-foreground">Provider:</span>
					<Badge variant="secondary" class="text-xs">{model.provider}</Badge>
				</div>
				<div class="ml-auto flex gap-1">
					<Button variant="ghost" size="sm" class="h-7 text-xs gap-1" onclick={() => exportJson(model)}>
						<Download class="h-3 w-3" />
						<span class="hidden sm:inline">Export</span>
					</Button>
					<Button variant="ghost" size="sm" class="h-7 text-xs gap-1" onclick={() => copyToClipboard(JSON.stringify({ ...model, capabilities_json: model.capabilities_json }, null, 2))}>
						<Copy class="h-3 w-3" />
						<span class="hidden sm:inline">Copy</span>
					</Button>
				</div>
			</div>
		</Card.Content>
	</Card.Root>

	<!-- Data Tables -->
	<div class="grid gap-3 lg:grid-cols-2">
		<!-- OpenRouter Metadata -->
		{#if Object.keys(caps).length}
			<Card.Root>
				<Card.Header class="pb-2">
					<Card.Title class="text-sm font-medium flex items-center gap-2">
						<Code class="h-4 w-4 text-purple-500" />
						OpenRouter Metadata
						<Badge variant="outline" class="text-xs">{Object.keys(caps).length} keys</Badge>
					</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="max-h-[300px] overflow-y-auto">
						<table class="w-full">
							<tbody class="divide-y">
								{#each Object.entries(caps).filter(([, v]) => v !== null && v !== '') as [key, value]}
									<tr class="hover:bg-muted/30">
										<td class="px-3 py-1.5 text-xs text-muted-foreground w-2/5"><code>{key}</code></td>
										<td class="px-3 py-1.5 text-xs">{formatMetaValue(value)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}

		<!-- Enriched Metadata -->
		{#if Object.keys(meta).length}
			<Card.Root>
				<Card.Header class="pb-2">
					<Card.Title class="text-sm font-medium flex items-center gap-2">
						<Layers class="h-4 w-4 text-emerald-500" />
						Enriched Dataset
						<Badge variant="outline" class="text-xs">{Object.keys(meta).length} keys</Badge>
					</Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="max-h-[300px] overflow-y-auto">
						<table class="w-full">
							<tbody class="divide-y">
								{#each Object.entries(meta).filter(([, v]) => v !== null && v !== '') as [key, value]}
									<tr class="hover:bg-muted/30">
										<td class="px-3 py-1.5 text-xs text-muted-foreground w-2/5"><code>{key}</code></td>
										<td class="px-3 py-1.5 text-xs">{formatMetaValue(value)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}
	</div>

	<!-- Raw JSON Payload -->
	<Card.Root>
		<Card.Content class="p-3">
			<button
				class="flex w-full items-center gap-2 text-left"
				onclick={() => rawJsonOpen = !rawJsonOpen}
			>
				<Code class="h-4 w-4 text-muted-foreground" />
				<span class="text-sm font-bold">Raw JSON Payload</span>
				<ChevronDown class="h-4 w-4 text-muted-foreground ml-auto transition-transform {rawJsonOpen ? 'rotate-180' : ''}" />
			</button>
			{#if rawJsonOpen}
				<div class="mt-3 relative">
					<Button
						variant="ghost"
						size="sm"
						class="absolute top-2 right-2 h-7 text-xs gap-1 bg-background/80 backdrop-blur"
						onclick={() => copyToClipboard(JSON.stringify(caps, null, 2))}
					>
						<Copy class="h-3 w-3" />
						Copy
					</Button>
					<pre class="rounded-lg bg-zinc-950 dark:bg-zinc-900 text-zinc-300 p-4 text-xs overflow-auto max-h-[400px]">{JSON.stringify(caps, null, 2)}</pre>
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
