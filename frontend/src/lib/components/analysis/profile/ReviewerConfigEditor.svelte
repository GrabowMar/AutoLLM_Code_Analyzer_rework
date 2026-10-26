<script lang="ts">
	/**
	 * ReviewerConfigEditor — the AI reviewer section of an analysis profile.
	 *
	 * Props:
	 * - `config` (`ReviewerConfig`): replaced, never mutated.
	 * - `options` (`ReviewerOptions` from `/reviewer/options/` or the profile
	 *   defaults): engines, harnesses with option schemas, limits, vocabularies.
	 * - `onchange(config)`: the next reviewer config after each edit.
	 * - `errors`: messages keyed relative to the reviewer (`samples`,
	 *   `budget.max_cost_usd`, `provider.order`; `""` for the section).
	 * - `readonly`, `idPrefix` (default 'reviewer').
	 *
	 * The model is picked from the catalog (generator ModelPicker, single
	 * mode); reasoning efforts follow the chosen model's vocabulary. AI
	 * findings are opinions: they are replicated `samples` times and only
	 * grounded consensus findings are counted, separately from static tools.
	 */
	import type {
		FileKind,
		FileOrigin,
		ProviderRouting,
		ReasoningEffort,
		ReviewerConfig,
		ReviewerEngine,
		ReviewerOptions
	} from '$lib/api/analysis';
	import type { HarnessOptions } from '$lib/api/generation';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Switch } from '$lib/components/ui/switch';
	import { Textarea } from '$lib/components/ui/textarea';
	import * as Select from '$lib/components/ui/select';
	import HarnessOptionsEditor from '$lib/components/generator/HarnessOptionsEditor.svelte';
	import OpenRouterKeyBanner from '$lib/components/generator/OpenRouterKeyBanner.svelte';
	import ChoiceChecklist from './ChoiceChecklist.svelte';
	import NumberField from './NumberField.svelte';
	import ReviewerModelField, { type ReviewerModelInfo } from './ReviewerModelField.svelte';
	import { invalidFieldsContext } from './invalid-fields.svelte';
	import { KIND_INFO, ORIGIN_INFO, errorsUnder, sameValue } from './profile-utils';
	import Bot from '@lucide/svelte/icons/bot';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		config: ReviewerConfig;
		options: ReviewerOptions | null;
		onchange?: (config: ReviewerConfig) => void;
		errors?: Record<string, string>;
		readonly?: boolean;
		idPrefix?: string;
	}

	let {
		config,
		options,
		onchange,
		errors = {},
		readonly = false,
		idPrefix = 'reviewer'
	}: Props = $props();

	/** Reasoning models spend output tokens on reasoning first (observed: 16k truncated a review, 32k sufficed). */
	const REASONING_MIN_OUTPUT_TOKENS = 32_000;

	const tracker = invalidFieldsContext();
	const defaultModel = $derived(
		options?.default_model ?? options?.defaults.model ?? 'deepseek/deepseek-v4.1-flash'
	);
	const limits = $derived(options?.limits);
	const harness = $derived(options?.harnesses.find((h) => h.name === config.harness) ?? null);
	const engineInfo = $derived(options?.engines.find((e) => e.name === config.engine) ?? null);
	const isAgent = $derived(config.engine === 'agent');

	let modelInfo = $state<ReviewerModelInfo>({ state: 'loading', model: null, reasoning: null });
	let harnessInvalid = $state(false);
	let advancedOpen = $state(false);

	$effect(() => {
		tracker?.set(`${idPrefix}-harness-options`, harnessInvalid && isAgent && config.enabled);
		return () => tracker?.set(`${idPrefix}-harness-options`, false);
	});

	function set<K extends keyof ReviewerConfig>(key: K, value: ReviewerConfig[K]) {
		onchange?.({ ...config, [key]: value });
	}

	function patch(next: Partial<ReviewerConfig>) {
		onchange?.({ ...config, ...next });
	}

	function setEngine(engine: string) {
		if (engine === config.engine) return;
		if (engine === 'agent') {
			const first = options?.harnesses[0]?.name ?? null;
			patch({
				engine: 'agent',
				harness: config.harness ?? first,
				harness_options: config.harness ? config.harness_options : {}
			});
		} else {
			// The single-shot engine has no harness; the backend drops it too.
			patch({ engine: engine as ReviewerEngine, harness: null, harness_options: {} });
		}
	}

	function setHarness(name: string) {
		if (name === config.harness) return;
		// Options are harness-specific: start from the new harness's defaults.
		patch({ harness: name || null, harness_options: {} });
	}

	// ── reasoning effort per model vocabulary ─────────────────────────
	const effortVocabulary = $derived((options?.reasoning_efforts ?? []) as string[]);
	const modelEfforts = $derived(modelInfo.reasoning?.efforts ?? []);
	const effortsKnown = $derived(modelEfforts.length > 0);
	const effortItems = $derived(
		effortVocabulary
			.filter((e) => !effortsKnown || modelEfforts.includes(e))
			.map((e) => ({
				value: e,
				label: e === modelInfo.reasoning?.defaultEffort ? `${e} (model default)` : e
			}))
	);
	const effortUnsupported = $derived(
		config.reasoning_effort !== null &&
			effortsKnown &&
			!modelEfforts.includes(config.reasoning_effort)
	);
	const isReasoningModel = $derived(
		!!modelInfo.model?.supports_reasoning || effortsKnown || !!modelInfo.reasoning?.mandatory
	);
	const lowOutputWarning = $derived(
		isReasoningModel &&
			config.reasoning_effort !== 'none' &&
			config.max_output_tokens < REASONING_MIN_OUTPUT_TOKENS
	);
	const overModelLimit = $derived(
		!!modelInfo.model &&
			modelInfo.model.max_output_tokens > 0 &&
			config.max_output_tokens > modelInfo.model.max_output_tokens
	);
	const defaultEffortText = $derived(
		modelInfo.reasoning?.defaultEffort ?? (isReasoningModel ? 'unknown' : 'none')
	);

	// ── provider routing JSON (advanced) ──────────────────────────────
	function providerText(provider: ProviderRouting | null): string {
		return provider && Object.keys(provider).length > 0 ? JSON.stringify(provider, null, 2) : '';
	}

	let providerDraft = $state('');
	let providerError = $state('');
	// The last value this editor emitted: an external change resets the draft.
	let providerSynced: ProviderRouting | null | undefined = undefined;

	$effect(() => {
		const provider = config.provider;
		if (providerSynced !== undefined && sameValue(provider, providerSynced)) return;
		providerSynced = provider;
		providerDraft = providerText(provider);
		providerError = '';
	});

	$effect(() => {
		tracker?.set(`${idPrefix}-provider`, !!providerError && config.enabled);
		return () => tracker?.set(`${idPrefix}-provider`, false);
	});

	function onProviderInput(text: string) {
		providerDraft = text;
		const trimmed = text.trim();
		if (!trimmed) {
			providerError = '';
			providerSynced = null;
			set('provider', null);
			return;
		}
		let parsed: unknown;
		try {
			parsed = JSON.parse(trimmed);
		} catch (e) {
			providerError = `Invalid JSON: ${(e as Error).message}`;
			return;
		}
		if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
			providerError = 'Provider routing must be a JSON object, e.g. {"order": ["deepinfra"]}';
			return;
		}
		providerError = '';
		providerSynced = parsed as ProviderRouting;
		set('provider', parsed as ProviderRouting);
	}

	const fieldError = (key: string) => errors[key] ?? '';
	const harnessErrors = $derived(errorsUnder(errors, 'harness_options'));
	const providerServerErrors = $derived(Object.entries(errorsUnder(errors, 'provider')));
	const advancedErrorCount = $derived(
		[
			'seed_base',
			'chunk_char_budget',
			'include_origins',
			'include_kinds',
			'prompt_version',
			'schema_version'
		].filter((k) => errors[k]).length +
			providerServerErrors.length +
			(providerError ? 1 : 0)
	);

	$effect(() => {
		if (advancedErrorCount > 0) advancedOpen = true;
	});

	const originItems = $derived(
		(options?.include_origins.choices ?? (Object.keys(ORIGIN_INFO) as FileOrigin[])).map((o) => ({
			value: o,
			label: ORIGIN_INFO[o]?.label ?? o,
			description: ORIGIN_INFO[o]?.description
		}))
	);
	const kindItems = $derived(
		(
			options?.include_kinds.choices ??
			(['source', 'config', 'manifest', 'env', 'doc', 'other'] as FileKind[])
		).map((k) => ({
			value: k,
			label: KIND_INFO[k]?.label ?? k,
			description: KIND_INFO[k]?.description
		}))
	);

	const id = (name: string) => `${idPrefix}-${name}`;
</script>

{#snippet fieldLabel(name: string, text: string, hint?: string)}
	<div class="flex flex-wrap items-baseline gap-x-2">
		<Label for={id(name)} class="text-xs">{text}</Label>
		{#if hint}<span class="text-[11px] text-muted-foreground">{hint}</span>{/if}
	</div>
{/snippet}

<div class="space-y-5" data-slot="reviewer-config-editor">
	<div
		class="flex flex-col gap-3 rounded-md border border-border bg-card px-3 py-3 sm:flex-row sm:items-start"
	>
		<Bot class="mt-0.5 hidden size-5 shrink-0 text-muted-foreground sm:block" aria-hidden="true" />
		<div class="min-w-0 flex-1 space-y-1">
			<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
				<label
					class={cn(
						'flex items-center gap-2 text-sm font-medium',
						readonly ? 'cursor-default' : 'cursor-pointer'
					)}
				>
					<Switch
						id={id('enabled')}
						checked={config.enabled}
						label="AI reviewer enabled"
						disabled={readonly}
						onCheckedChange={(on) => set('enabled', on)}
					/>
					AI reviewer
				</label>
				<label
					class={cn(
						'flex items-center gap-2 text-xs',
						readonly || !config.enabled ? 'cursor-default' : 'cursor-pointer',
						!config.enabled && 'opacity-60'
					)}
				>
					<Switch
						id={id('required')}
						checked={config.enabled && config.required}
						size="sm"
						label="AI reviewer required"
						disabled={readonly || !config.enabled}
						onCheckedChange={(on) => set('required', on)}
					/>
					<span
						class={config.required && config.enabled
							? 'font-medium text-foreground'
							: 'text-muted-foreground'}>Required</span
					>
				</label>
			</div>
			<p class="text-xs leading-relaxed text-muted-foreground">
				{#if config.enabled}
					AI findings are opinions: each run reviews the code {config.samples} time{config.samples ===
					1
						? ''
						: 's'}, grounds every finding against the cited lines and counts only grounded findings
					a majority of replicates agree on — always reported apart from static tool findings.{config.required
						? ' A failed review makes the run partial.'
						: ' A failed review leaves the AI measurement missing without blocking completeness.'}
				{:else}
					Off: runs measure static tools only. Settings below are kept for when you switch it back
					on.
				{/if}
			</p>
			{#if fieldError('enabled') || fieldError('required') || fieldError('')}
				<p class="text-[11px] text-danger-fg">
					{fieldError('enabled') || fieldError('required') || fieldError('')}
				</p>
			{/if}
		</div>
	</div>

	{#if !options}
		<div
			class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm"
			role="alert"
		>
			<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
			<p class="text-muted-foreground">
				Reviewer options are unavailable, so engines, harnesses and limits cannot be shown. The
				stored settings are kept.
			</p>
		</div>
	{:else if config.enabled}
		{#if !readonly}
			<OpenRouterKeyBanner hideWhenOk />
		{/if}

		<!-- Engine + harness -->
		<section class="space-y-3" aria-labelledby={id('engine-heading')}>
			<h4 id={id('engine-heading')} class="section-label">Engine</h4>
			<Segmented
				aria-labelledby={id('engine-heading')}
				value={config.engine}
				disabled={readonly}
				items={options.engines.map((e) => ({
					value: e.name,
					label: e.label,
					title: e.description
				}))}
				onValueChange={setEngine}
			/>
			{#if engineInfo}
				<p class="text-xs leading-relaxed text-muted-foreground">{engineInfo.description}</p>
			{/if}
			{#if fieldError('engine')}<p class="text-[11px] text-danger-fg">
					{fieldError('engine')}
				</p>{/if}

			{#if isAgent}
				<div class="space-y-3 rounded-md border border-border bg-muted/20 p-3">
					<div class="space-y-1.5">
						{@render fieldLabel('harness', 'Harness')}
						<Select.Simple
							id={id('harness')}
							size="sm"
							class="sm:max-w-sm"
							value={config.harness ?? ''}
							placeholder="Pick a harness"
							items={options.harnesses.map((h) => ({
								value: h.name,
								label: `${h.label} ${h.version}`,
								description: h.description
							}))}
							aria-invalid={fieldError('harness') ? true : undefined}
							disabled={readonly}
							onValueChange={setHarness}
						/>
						{#if fieldError('harness')}<p class="text-[11px] text-danger-fg">
								{fieldError('harness')}
							</p>{/if}
					</div>
					{#if harness}
						<div class="space-y-1.5 text-xs">
							<p class="leading-relaxed text-muted-foreground">{harness.description}</p>
							<div class="flex flex-wrap gap-1.5">
								<span
									class="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
								>
									findings via {harness.output_channel === 'file'
										? 'a findings file'
										: 'its final answer'}
								</span>
								{#if harness.requires_tool_calling}
									<span
										class="rounded-full border border-border px-2 py-0.5 text-[11px] text-muted-foreground"
										>needs tool calling</span
									>
								{/if}
							</div>
							{#if harness.notes.length > 0}
								<ul class="list-disc space-y-0.5 pl-4 text-[11px] leading-4 text-muted-foreground">
									{#each harness.notes as note, i (i)}<li>{note}</li>{/each}
								</ul>
							{/if}
						</div>
						<div class="border-t border-border pt-3">
							<HarnessOptionsEditor
								schema={harness.option_schema}
								options={config.harness_options}
								defaults={harness.default_options}
								idPrefix={id('harness-opt')}
								disabled={readonly}
								errors={harnessErrors}
								bind:invalid={harnessInvalid}
								onchange={(next: HarnessOptions) => set('harness_options', next)}
							/>
						</div>
					{/if}
				</div>
			{/if}
		</section>

		<!-- Model -->
		<section class="space-y-2" aria-labelledby={id('model-heading')}>
			<h4 id={id('model-heading')} class="section-label">Model</h4>
			<ReviewerModelField
				id={id('model')}
				model={config.model}
				{defaultModel}
				requireTools={isAgent && !!harness?.requires_tool_calling}
				{readonly}
				error={fieldError('model')}
				bind:info={modelInfo}
				onchange={(model) => set('model', model)}
			/>
		</section>

		<!-- Sampling -->
		<section class="space-y-3" aria-labelledby={id('sampling-heading')}>
			<h4 id={id('sampling-heading')} class="section-label">Replicates and sampling</h4>
			<div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
				<div class="space-y-1.5">
					{@render fieldLabel(
						'samples',
						'Replicates (k)',
						`quorum ${Math.ceil(config.samples / 2)} of ${config.samples}`
					)}
					<NumberField
						id={id('samples')}
						value={config.samples}
						integer
						min={limits?.samples.min}
						max={limits?.samples.max}
						error={fieldError('samples')}
						disabled={readonly}
						describedBy={id('samples-help')}
						onchange={(n) => n !== null && set('samples', n)}
					/>
					<p id={id('samples-help')} class="text-[11px] leading-4 text-muted-foreground">
						k ≥ 3 gives agreement statistics; cost grows linearly.
					</p>
				</div>
				<div class="space-y-1.5">
					{@render fieldLabel('temperature', 'Temperature')}
					<NumberField
						id={id('temperature')}
						value={config.temperature}
						nullable
						step={0.05}
						min={limits?.temperature.min}
						max={limits?.temperature.max}
						placeholder="provider default"
						error={fieldError('temperature')}
						disabled={readonly}
						describedBy={id('temperature-help')}
						onchange={(n) => set('temperature', n)}
					/>
					<p id={id('temperature-help')} class="text-[11px] leading-4 text-muted-foreground">
						Empty = the provider's default.
					</p>
				</div>
				<div class="space-y-1.5">
					{@render fieldLabel('effort', 'Reasoning effort')}
					<Select.Simple
						id={id('effort')}
						size="sm"
						value={config.reasoning_effort ?? ''}
						items={effortItems}
						placeholder="Model default ({defaultEffortText})"
						clearLabel="Model default ({defaultEffortText})"
						aria-invalid={fieldError('reasoning_effort') || effortUnsupported ? true : undefined}
						aria-describedby={id('effort-help')}
						disabled={readonly}
						onValueChange={(v) => set('reasoning_effort', (v || null) as ReasoningEffort | null)}
					/>
					<p
						id={id('effort-help')}
						class={cn(
							'text-[11px] leading-4',
							fieldError('reasoning_effort') ? 'text-danger-fg' : 'text-muted-foreground'
						)}
					>
						{#if fieldError('reasoning_effort')}
							{fieldError('reasoning_effort')}
						{:else if modelInfo.state === 'ready' && modelInfo.model && effortsKnown}
							{modelInfo.model.model_name} accepts {modelEfforts.join(', ')}.
						{:else if modelInfo.state === 'ready' && modelInfo.model && modelInfo.reasoning}
							No effort levels listed for this model.
						{:else}
							Checked against the model when a plan is frozen.
						{/if}
					</p>
				</div>
				<div class="space-y-1.5">
					{@render fieldLabel('max-tokens', 'Max output tokens', 'per call')}
					<NumberField
						id={id('max-tokens')}
						value={config.max_output_tokens}
						integer
						step={1000}
						min={limits?.max_output_tokens.min}
						max={limits?.max_output_tokens.max}
						error={fieldError('max_output_tokens')}
						disabled={readonly}
						describedBy={id('max-tokens-help')}
						onchange={(n) => n !== null && set('max_output_tokens', n)}
					/>
					<p id={id('max-tokens-help')} class="text-[11px] leading-4 text-muted-foreground">
						Bounds reasoning and answer; a truncated replicate fails.
					</p>
				</div>
			</div>

			{#if effortUnsupported}
				<div
					class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs"
					role="alert"
				>
					<TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
					<p>
						<span class="font-medium text-warning-fg"
							>Effort “{config.reasoning_effort}” is not supported by this model.</span
						>
						<span class="text-muted-foreground"
							>Freezing a plan rejects it; pick one of {modelEfforts.join(', ')} or the model default.</span
						>
					</p>
				</div>
			{/if}
			{#if lowOutputWarning}
				<div
					class="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs"
					role="alert"
				>
					<TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-warning-fg" aria-hidden="true" />
					<p>
						<span class="font-medium text-warning-fg"
							>Below {REASONING_MIN_OUTPUT_TOKENS.toLocaleString()} tokens for a reasoning model.</span
						>
						<span class="text-muted-foreground">
							Reasoning spends output tokens before the answer; a review truncated at 16k was
							observed, so replicates may fail.
						</span>
					</p>
				</div>
			{/if}
			{#if overModelLimit && modelInfo.model}
				<p class="text-[11px] text-muted-foreground">
					Above the model's output limit ({modelInfo.model.max_output_tokens.toLocaleString()} tokens);
					the provider caps it.
				</p>
			{/if}
		</section>

		<!-- Budget -->
		<section class="space-y-3" aria-labelledby={id('budget-heading')}>
			<div class="flex flex-wrap items-baseline gap-x-2">
				<h4 id={id('budget-heading')} class="section-label">Budget</h4>
				<span class="text-[11px] text-muted-foreground"
					>per replicate — a replicate over budget fails, never truncates silently</span
				>
			</div>
			<div class="grid gap-4 sm:grid-cols-3">
				<div class="space-y-1.5">
					{@render fieldLabel('budget-cost', 'Max cost')}
					<NumberField
						id={id('budget-cost')}
						value={config.budget.max_cost_usd}
						step={0.05}
						min={limits?.budget.max_cost_usd.min}
						max={limits?.budget.max_cost_usd.max}
						unit="USD"
						error={fieldError('budget.max_cost_usd')}
						disabled={readonly}
						onchange={(n) => n !== null && set('budget', { ...config.budget, max_cost_usd: n })}
					/>
				</div>
				<div class="space-y-1.5">
					{@render fieldLabel('budget-requests', 'Max LLM requests')}
					<NumberField
						id={id('budget-requests')}
						value={config.budget.max_llm_requests}
						integer
						min={limits?.budget.max_llm_requests.min}
						max={limits?.budget.max_llm_requests.max}
						error={fieldError('budget.max_llm_requests')}
						disabled={readonly}
						onchange={(n) => n !== null && set('budget', { ...config.budget, max_llm_requests: n })}
					/>
				</div>
				<div class="space-y-1.5">
					{@render fieldLabel('budget-wall', 'Wall clock')}
					<NumberField
						id={id('budget-wall')}
						value={config.budget.wall_clock_seconds}
						integer
						min={limits?.budget.wall_clock_seconds.min}
						max={limits?.budget.wall_clock_seconds.max}
						unit="s"
						error={fieldError('budget.wall_clock_seconds')}
						disabled={readonly}
						onchange={(n) =>
							n !== null && set('budget', { ...config.budget, wall_clock_seconds: n })}
					/>
				</div>
			</div>
			{#if fieldError('budget')}<p class="text-[11px] text-danger-fg">
					{fieldError('budget')}
				</p>{/if}
		</section>

		<!-- Advanced -->
		<section class="rounded-md border border-border">
			<button
				type="button"
				class="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left text-sm font-medium transition-colors hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40"
				aria-expanded={advancedOpen}
				aria-controls={id('advanced')}
				onclick={() => (advancedOpen = !advancedOpen)}
			>
				<ChevronRight
					class={cn(
						'size-4 shrink-0 text-muted-foreground transition-transform duration-150',
						advancedOpen && 'rotate-90'
					)}
					aria-hidden="true"
				/>
				Advanced
				<span class="text-[11px] font-normal text-muted-foreground"
					>scope, prompt, seed, chunking, provider routing</span
				>
				{#if advancedErrorCount > 0}
					<span
						class="ml-auto inline-flex items-center gap-1 text-[11px] font-medium text-danger-fg"
					>
						<CircleAlert class="size-3" aria-hidden="true" />{advancedErrorCount}
					</span>
				{/if}
			</button>
			{#if advancedOpen}
				<div id={id('advanced')} class="space-y-5 border-t border-border px-3 py-4">
					<div class="space-y-2">
						<span id={id('origins-label')} class="text-xs font-medium"
							>Files sent to the reviewer — origins</span
						>
						<ChoiceChecklist
							id={id('origins')}
							labelledBy={id('origins-label')}
							items={originItems}
							value={config.include_origins}
							disabled={readonly}
							error={fieldError('include_origins')}
							columns="grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
							onchange={(values) => set('include_origins', values as FileOrigin[])}
						/>
					</div>
					<div class="space-y-2">
						<span id={id('kinds-label')} class="text-xs font-medium"
							>Files sent to the reviewer — kinds</span
						>
						<ChoiceChecklist
							id={id('kinds')}
							labelledBy={id('kinds-label')}
							items={kindItems}
							value={config.include_kinds}
							disabled={readonly}
							error={fieldError('include_kinds')}
							columns="grid-cols-1 sm:grid-cols-2 lg:grid-cols-3"
							onchange={(values) => set('include_kinds', values as FileKind[])}
						/>
						<p class="text-[11px] leading-4 text-muted-foreground">
							Findings are counted only inside the profile's measured scope; files outside it are
							still reviewed for context.
						</p>
					</div>

					<div class="grid gap-4 sm:grid-cols-2">
						<div class="space-y-1.5">
							{@render fieldLabel('prompt', 'Prompt version')}
							<Select.Simple
								id={id('prompt')}
								size="sm"
								value={config.prompt_version}
								items={options.prompt_versions.map((p) => ({
									value: p.version,
									label: p.version,
									description: `sha256 ${p.sha256.slice(0, 12)}`
								}))}
								aria-invalid={fieldError('prompt_version') ? true : undefined}
								disabled={readonly}
								onValueChange={(v) => set('prompt_version', v)}
							/>
							{#if fieldError('prompt_version')}<p class="text-[11px] text-danger-fg">
									{fieldError('prompt_version')}
								</p>{/if}
						</div>
						<div class="space-y-1.5">
							{@render fieldLabel('schema', 'Findings schema')}
							<Select.Simple
								id={id('schema')}
								size="sm"
								value={config.schema_version}
								items={options.schema_versions.map((s) => ({
									value: s.version,
									label: s.version,
									description: `sha256 ${s.sha256.slice(0, 12)}`
								}))}
								aria-invalid={fieldError('schema_version') ? true : undefined}
								disabled={readonly}
								onValueChange={(v) => set('schema_version', v)}
							/>
							{#if fieldError('schema_version')}<p class="text-[11px] text-danger-fg">
									{fieldError('schema_version')}
								</p>{/if}
						</div>
						<div class="space-y-1.5">
							{@render fieldLabel('seed', 'Seed base', 'per-replicate file order')}
							<NumberField
								id={id('seed')}
								value={config.seed_base}
								integer
								min={limits?.seed_base.min}
								max={limits?.seed_base.max}
								error={fieldError('seed_base')}
								disabled={readonly}
								onchange={(n) => n !== null && set('seed_base', n)}
							/>
						</div>
						<div class="space-y-1.5">
							{@render fieldLabel(
								'chunk',
								'Chunk budget',
								isAgent ? 'single-shot only' : 'characters per call'
							)}
							<NumberField
								id={id('chunk')}
								value={config.chunk_char_budget}
								integer
								step={1000}
								min={limits?.chunk_char_budget.min}
								max={limits?.chunk_char_budget.max}
								unit="chars"
								error={fieldError('chunk_char_budget')}
								disabled={readonly}
								onchange={(n) => n !== null && set('chunk_char_budget', n)}
							/>
						</div>
					</div>

					<div class="space-y-1.5">
						{@render fieldLabel('provider', 'Provider routing', 'OpenRouter JSON, optional')}
						<Textarea
							id={id('provider')}
							value={providerDraft}
							rows={4}
							placeholder={'{"order": ["deepinfra", "novita"], "allow_fallbacks": false}'}
							class={cn(
								'font-mono text-xs',
								(providerError || providerServerErrors.length > 0) && 'border-destructive'
							)}
							aria-invalid={providerError || providerServerErrors.length > 0 ? true : undefined}
							aria-describedby={id('provider-help')}
							spellcheck="false"
							disabled={readonly}
							oninput={(e: Event) =>
								onProviderInput((e.currentTarget as HTMLTextAreaElement).value)}
						/>
						<div id={id('provider-help')} class="space-y-0.5 text-[11px] leading-4">
							{#if providerError}
								<p class="text-danger-fg">{providerError}</p>
							{/if}
							{#each providerServerErrors as [key, message] (key)}
								<p class="text-danger-fg">{key ? `${key}: ` : ''}{message}</p>
							{/each}
							<p class="text-muted-foreground">
								Empty uses the platform's default routing (recorded in each frozen plan). Keys:
								order, only, ignore, quantizations, allow_fallbacks, require_parameters, zdr, sort,
								data_collection, max_price.
							</p>
						</div>
					</div>
				</div>
			{/if}
		</section>
	{/if}
</div>
