<script lang="ts">
	/**
	 * Capabilities tab: features, modalities, reasoning controls, every supported
	 * request parameter, limits and defaults — from the OpenRouter payload, the
	 * models.dev record and (local models) the Ollama inventory.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import Check from '@lucide/svelte/icons/check';
	import X from '@lucide/svelte/icons/x';
	import ArrowDownToLine from '@lucide/svelte/icons/arrow-down-to-line';
	import ArrowUpFromLine from '@lucide/svelte/icons/arrow-up-from-line';
	import type { LLMModelDetail } from '$lib/api/client';
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
	} from './helpers';

	interface Props {
		model: LLMModelDetail;
	}

	let { model }: Props = $props();

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
</script>

<div class="space-y-4">
	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Features</Card.Title></Card.Header>
			<Card.Content class="space-y-4">
				<ul class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-3">
					{#each features as f (f.label)}
						<li class="flex items-center gap-1.5 {f.on ? '' : 'text-muted-foreground'}" title={f.note}>
							{#if f.on}<Check class="size-3.5 text-success-fg" aria-hidden="true" />{:else}<X class="size-3.5 opacity-60" aria-hidden="true" />{/if}
							<span>{f.label}</span>
							<span class="sr-only">{f.on ? 'supported' : 'not supported'}</span>
						</li>
					{/each}
				</ul>
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

		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Reasoning</Card.Title></Card.Header>
			<Card.Content class="space-y-3 text-sm">
				{#if !reasoning && !mdReasoning.length}
					<p class="text-muted-foreground">No reasoning controls published.</p>
				{:else}
					{#if reasoning}
						<dl class="grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-2">
							<dt class="text-muted-foreground">Always on</dt>
							<dd>{reasoning.mandatory ? 'Yes — cannot be disabled' : 'No — optional'}</dd>
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
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<Card.Root>
		<Card.Header class="pb-0">
			<Card.Title class="text-sm font-medium">Request parameters <span class="font-mono text-xs text-muted-foreground">{params.length}</span></Card.Title>
			<Card.Description class="text-xs">What OpenRouter forwards for this model; anything else is dropped. Hover for what a parameter does.</Card.Description>
		</Card.Header>
		<Card.Content>
			{#if params.length}
				<div class="flex flex-wrap gap-1.5">
					{#each [...params].sort() as p (p)}
						<Badge variant="outline" class="font-mono text-[11px]" title={PARAM_HELP[p] ?? p}>{p}</Badge>
					{/each}
				</div>
			{:else}
				<p class="text-sm text-muted-foreground">Not published.</p>
			{/if}
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 {ollama?.capabilities ? 'lg:grid-cols-3' : 'lg:grid-cols-2'}">
		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Limits</Card.Title></Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 text-sm">
					<dt class="text-muted-foreground">Context window</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(model.context_window)}</dd>
					<dt class="text-muted-foreground">Max output</dt><dd class="font-mono text-xs tabular-nums">{model.max_output_tokens ? formatNumber(model.max_output_tokens) : '—'}</dd>
					{#if md?.limit?.input}<dt class="text-muted-foreground">Max input</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(md.limit.input)}</dd>{/if}
					{#if limits.prompt_tokens}<dt class="text-muted-foreground">Per request · prompt</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(limits.prompt_tokens)}</dd>{/if}
					{#if limits.completion_tokens}<dt class="text-muted-foreground">Per request · completion</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(limits.completion_tokens)}</dd>{/if}
					{#if model.endpoint && ollama?.context_length}<dt class="text-muted-foreground">Native context</dt><dd class="font-mono text-xs tabular-nums">{formatNumber(Number(ollama.context_length))}</dd>{/if}
				</dl>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Default parameters</Card.Title></Card.Header>
			<Card.Content>
				{#if defaults.length}
					<dl class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1.5 text-sm">
						{#each defaults as [k, v] (k)}
							<dt class="font-mono text-xs text-muted-foreground">{k}</dt><dd class="font-mono text-xs">{String(v)}</dd>
						{/each}
					</dl>
				{:else}
					<p class="text-sm text-muted-foreground">Provider defaults (none published).</p>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if ollama?.capabilities}
			<Card.Root>
				<Card.Header class="pb-0"><Card.Title class="text-sm font-medium">Ollama capabilities</Card.Title></Card.Header>
				<Card.Content><p class="font-mono text-xs">{(ollama.capabilities as string[]).join(', ')}</p></Card.Content>
			</Card.Root>
		{/if}
	</div>
</div>
