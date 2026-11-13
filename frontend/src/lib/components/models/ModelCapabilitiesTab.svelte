<script lang="ts">
	/**
	 * Capabilities tab: features, modalities, reasoning controls, every supported
	 * request parameter, limits, the repo architecture (config.json) and the
	 * sampling defaults comparison (authors vs OpenRouter vs your runs) — from the
	 * OpenRouter payload, the models.dev record, Hugging Face and (local models)
	 * the endpoint inventory, with a local model's catalog-twin data marked.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import ArrowDownToLine from '@lucide/svelte/icons/arrow-down-to-line';
	import ArrowUpFromLine from '@lucide/svelte/icons/arrow-up-from-line';
	import type { LLMModelDetail } from '$lib/api/client';
	import ModelArchitectureCard from './ModelArchitectureCard.svelte';
	import ModelSamplingDefaults from './ModelSamplingDefaults.svelte';
	import {
		formatNumber,
		getArchitecture,
		getCaps,
		getDefaultParameters,
		getMeta,
		getModelsDev,
		getPerRequestLimits,
		getReasoning,
		getSupportedParameters,
		metadataView,
		knownTokenLimit,
		type ReasoningInfo,
	} from './helpers';

	interface Props {
		model: LLMModelDetail;
		slug: string;
	}

	let { model, slug }: Props = $props();

	// What each OpenRouter request parameter controls, for the hover text.
	const PARAM_HELP: Record<string, string> = {
		tools: 'Function / tool calling',
		tool_choice: 'Force or forbid a tool call',
		response_format: 'JSON mode / JSON schema output',
		structured_outputs: 'Strict JSON-schema adherence',
		reasoning: 'Reasoning effort / token budget',
		include_reasoning: 'Return the reasoning trace',
		reasoning_effort: 'OpenAI-style effort level',
		temperature: 'Sampling temperature',
		top_p: 'Nucleus sampling',
		top_k: 'Top-k sampling',
		min_p: 'Min-p sampling',
		top_a: 'Top-a sampling',
		seed: 'Deterministic sampling seed',
		stop: 'Stop sequences',
		max_tokens: 'Output token cap',
		max_completion_tokens: 'Output token cap (OpenAI name)',
		frequency_penalty: 'Penalize frequent tokens',
		presence_penalty: 'Penalize repeated topics',
		repetition_penalty: 'Penalize repetition',
		logit_bias: 'Bias specific tokens',
		logprobs: 'Return token log-probabilities',
		top_logprobs: 'Alternatives per token',
		web_search_options: 'Built-in web search',
		verbosity: 'Answer length control',
		parallel_tool_calls: 'Several tool calls per turn',
	};

	const caps = $derived(getCaps(model));
	const arch = $derived(getArchitecture(model));
	const md = $derived(getModelsDev(model));
	const reasoning = $derived(getReasoning(model));
	const params = $derived(getSupportedParameters(model));
	const limits = $derived(getPerRequestLimits(model));
	const defaults = $derived(Object.entries(getDefaultParameters(model)).filter(([, v]) => v !== null && v !== undefined));
	const inputMods = $derived((arch.input_modalities as string[] | undefined) ?? md?.modalities?.input ?? []);
	const outputMods = $derived((arch.output_modalities as string[] | undefined) ?? md?.modalities?.output ?? []);
	const ollama = $derived((getMeta(model).ollama ?? null) as Record<string, unknown> | null);
	// Repo architecture / authors' sampling: own, or a local model's catalog twin (marked inherited).
	const view = $derived(metadataView(model));
	// OpenRouter defaults only describe OpenRouter serving: a local endpoint model has none.
	const orDefaults = $derived(model.is_local ? null : getDefaultParameters(model));
	const orReasoning = $derived<ReasoningInfo | null>(model.is_local ? null : reasoning);
	const voices = $derived((caps.supported_voices as string[] | null | undefined) ?? []);

	const features = $derived.by(() => {
		const rows: { label: string; on: boolean | undefined; note?: string }[] = [
			{ label: 'Tool calling', on: model.supports_function_calling || md?.tool_call },
			{ label: 'Structured output', on: params.includes('structured_outputs') || md?.structured_output, note: 'JSON schema' },
			{ label: 'JSON mode', on: model.supports_json_mode },
			{ label: 'Reasoning', on: !!reasoning || md?.reasoning || model.supports_reasoning },
			{ label: 'Vision input', on: model.supports_vision || inputMods.includes('image') },
			{ label: 'File / PDF input', on: inputMods.includes('file') || inputMods.includes('pdf') || md?.attachment },
			{ label: 'Audio', on: inputMods.includes('audio') || outputMods.includes('audio') },
			{ label: 'Temperature', on: md?.temperature ?? params.includes('temperature') },
			{ label: 'Streaming', on: model.supports_streaming || !model.is_local },
		];
		return rows;
	});

	const mdReasoning = $derived(md?.reasoning_options ?? []);
	const hasReasoning = $derived(!!reasoning || mdReasoning.length > 0);
	const supported = $derived(features.filter((f) => f.on));
	const unsupported = $derived(features.filter((f) => !f.on));
	const unpublished = $derived(
		[
			!hasReasoning && 'reasoning controls',
			!params.length && 'request parameters',
			!defaults.length && 'default parameters',
		].filter(Boolean) as string[],
	);
</script>

<div class="space-y-4">
	<div class="grid gap-4 {hasReasoning ? 'lg:grid-cols-2' : ''}">
		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Features</Card.Title></Card.Header>
			<Card.Content class="space-y-4">
				{#if supported.length}
					<ul class="flex flex-wrap gap-1.5" aria-label="Supported">
						{#each supported as f (f.label)}
							<li class="feat-on" title={f.note}><Check class="size-3.5" aria-hidden="true" />{f.label}</li>
						{/each}
					</ul>
				{:else}
					<p class="text-sm text-muted-foreground">No optional features reported.</p>
				{/if}
				{#if unsupported.length}
					<p class="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
						<X class="size-3 opacity-60" aria-hidden="true" /><span class="sr-only">Not supported:</span>
						{#each unsupported as f, i (f.label)}<span title={f.note}>{f.label}{i < unsupported.length - 1 ? ',' : ''}</span>{/each}
					</p>
				{/if}
				<div>
					<div class="section-label mb-2">Modalities</div>
					<div class="flex flex-wrap gap-1">
						{#each inputMods.length ? inputMods : ['text'] as mod (mod)}
							<Badge variant="outline" class="gap-1 text-xs"><ArrowDownToLine class="size-2.5" />{mod}</Badge>
						{/each}
						{#each outputMods.length ? outputMods : ['text'] as mod (mod)}
							<Badge variant="outline" class="gap-1 text-xs"><ArrowUpFromLine class="size-2.5" />{mod}</Badge>
						{/each}
						{#if arch.modality}<span class="ml-1 self-center font-mono text-[11px] text-muted-foreground">{arch.modality}</span>{/if}
					</div>
				</div>
				{#if voices.length}
					<div>
						<div class="section-label mb-2">Voices</div>
						<p class="font-mono text-xs">{voices.join(', ')}</p>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if hasReasoning}
		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Reasoning</Card.Title></Card.Header>
			<Card.Content class="space-y-3 text-sm">
					{#if reasoning}
						<dl class="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-2">
							<dt class="text-muted-foreground">Always on</dt>
							<dd>{reasoning.mandatory ? 'Yes — cannot be disabled' : 'No — optional'}</dd>
							{#if !reasoning.mandatory && reasoning.default_enabled != null}
								<dt class="text-muted-foreground">Default</dt>
								<dd title="What a request without reasoning settings gets (provider_default agent runs)">{reasoning.default_enabled ? 'On unless disabled' : 'Off unless requested'}</dd>
							{/if}
							{#if reasoning.default_effort}
								<dt class="text-muted-foreground">Default effort</dt>
								<dd class="font-mono text-xs leading-5">{reasoning.default_effort}</dd>
							{/if}
							{#if reasoning.supported_efforts?.length}
								<dt class="text-muted-foreground">Effort levels</dt>
								<dd class="flex flex-wrap gap-1">
									{#each reasoning.supported_efforts as e (e)}
										<Badge variant={e === reasoning.default_effort ? 'secondary' : 'outline'} class="font-mono text-[11px]">{e}</Badge>
									{/each}
								</dd>
							{/if}
						</dl>
					{/if}
					{#each mdReasoning as opt, i (i)}
						<p class="text-xs text-muted-foreground">
							models.dev: <span class="font-mono text-foreground">{opt.type}</span>
							{#if opt.values?.length}{opt.values.join(' / ')}{/if}
							{#if opt.min != null}min {formatNumber(opt.min)}{/if}{#if opt.max != null}, max {formatNumber(opt.max)}{/if}
						</p>
					{/each}
			</Card.Content>
		</Card.Root>
		{/if}
	</div>

	{#if params.length}
	<Card.Root>
		<Card.Header class="pb-0">
			<Card.Title class="text-sm font-medium">Request parameters <span class="font-mono text-xs text-muted-foreground">{params.length}</span></Card.Title>
			<Card.Description class="text-xs">What OpenRouter forwards for this model; anything else is dropped. Hover for what a parameter does.</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="flex flex-wrap gap-1.5">
				{#each [...params].sort() as p (p)}
					<Badge variant="outline" class="font-mono text-[11px]" title={PARAM_HELP[p] ?? p}>{p}</Badge>
				{/each}
			</div>
		</Card.Content>
	</Card.Root>
	{/if}

	<div class="grid gap-4 {[true, defaults.length > 0, !!ollama?.capabilities].filter(Boolean).length === 3 ? 'lg:grid-cols-3' : [defaults.length > 0, !!ollama?.capabilities].some(Boolean) ? 'lg:grid-cols-2' : ''}">
		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Limits</Card.Title></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 text-sm">
					<dt class="text-muted-foreground">Context window</dt><dd class="font-mono text-xs tabular-nums">{#if knownTokenLimit(model.context_window) != null}{formatNumber(model.context_window)}{:else}<span class="text-muted-foreground" title="Not reported by the source">—</span>{/if}</dd>
					<dt class="text-muted-foreground">Max output</dt><dd class="font-mono text-xs tabular-nums">{model.max_output_tokens ? formatNumber(model.max_output_tokens) : '—'}</dd>
					{#if md?.limit?.input}<dt class="text-muted-foreground">Max input</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(md.limit.input)}</dd>{/if}
					{#if limits.prompt_tokens}<dt class="text-muted-foreground">Per request · prompt</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(limits.prompt_tokens)}</dd>{/if}
					{#if limits.completion_tokens}<dt class="text-muted-foreground">Per request · completion</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(limits.completion_tokens)}</dd>{/if}
					{#if model.endpoint && ollama?.context_length}<dt class="text-muted-foreground">Native context</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(Number(ollama.context_length))}</dd>{/if}
				</dl>
				{#if md?.status}<p class="mt-2 text-xs text-muted-foreground">models.dev status: <span class="font-mono text-foreground">{md.status}</span></p>{/if}
			</Card.Content>
		</Card.Root>

		{#if defaults.length}
		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Default parameters</Card.Title></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 text-sm">
					{#each defaults as [k, v] (k)}
						<dt class="font-mono text-xs text-muted-foreground">{k}</dt><dd class="font-mono text-xs">{String(v)}</dd>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>
		{/if}

		{#if ollama?.capabilities}
			<Card.Root>
				<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Ollama capabilities</Card.Title></Card.Header>
				<Card.Content><p class="font-mono text-xs">{(ollama.capabilities as string[]).join(', ')}</p></Card.Content>
			</Card.Root>
		{/if}
	</div>

	<ModelSamplingDefaults
		{slug}
		authors={view.hf?.generation_config ?? null}
		authorsStatus={view.hf?.fetch_status?.generation_config ?? null}
		hasRepo={!!view.hf || !!view.caps.hugging_face_id}
		openrouterDefaults={orDefaults}
		reasoning={orReasoning}
		inheritedFrom={view.inheritedFrom}
		inheritedSlug={view.inheritedSlug}
	/>

	{#if unpublished.length}
		<p class="px-1 text-xs text-muted-foreground">
			Not published by the source{model.is_local ? ' (local endpoint)' : ''}: {unpublished.join(', ')}.
		</p>
	{/if}

	<ModelArchitectureCard
		hf={view.hf}
		params={view.params}
		hfId={(view.caps.hugging_face_id as string | undefined) ?? null}
		inheritedFrom={view.inheritedFrom}
		inheritedSlug={view.inheritedSlug}
	/>
</div>

<style>
	.feat-on {
		display: inline-flex;
		align-items: center;
		gap: 0.3rem;
		border-radius: 9999px;
		border: 1px solid color-mix(in oklab, var(--success) 35%, var(--border));
		background: color-mix(in oklab, var(--success) 8%, transparent);
		padding: 0.125rem 0.625rem 0.125rem 0.5rem;
		font-size: 0.8125rem;
	}
	.feat-on :global(svg) {
		color: var(--success-fg);
	}
</style>
