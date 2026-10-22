<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount, untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Textarea } from '$lib/components/ui/textarea';
	import {
		getAgentConfig,
		previewEffectiveLLMParams,
		type AgentConfigInfo,
		type BudgetPresetConfig,
		type LLMParams,
		type RunPreset,
		type SamplingPresetConfig,
	} from '$lib/api/generation';
	import { createExperiment, type ExperimentMode } from '$lib/api/experiments';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import {
		AppSpecPicker,
		LLMParamsEditor,
		PresetPicker,
		StackSelect,
		cleanLLMParams,
	} from '$lib/components/generator';
	import { numberInputValue } from '$lib/components/generator/llm-params';
	import BudgetFields from '$lib/components/templates/BudgetFields.svelte';
	import { slugify } from '$lib/components/templates/slugify';
	import { resolvePreset } from '$lib/components/templates/presets';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Bot from '@lucide/svelte/icons/bot';
	import Dices from '@lucide/svelte/icons/dices';
	import FileCode from '@lucide/svelte/icons/file-code';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	const MAX_SEED = 2 ** 31 - 1;

	let name = $state('');
	let slugInput = $state('');
	let slugTouched = $state(false);
	const slug = $derived(slugTouched ? slugInput : slugify(name));
	let description = $state('');
	let hypothesis = $state('');
	let mode = $state<ExperimentMode>('agent');
	let stackSlug = $state('');

	let samplingPresetId = $state<number | null>(null);
	let samplingPreset = $state<RunPreset | null>(null);
	let llmDefaults = $state<LLMParams>({});
	let llmInvalid = $state(false);

	let budgetPresetId = $state<number | null>(null);
	let budgetPreset = $state<RunPreset | null>(null);
	let budgetOverrides = $state<BudgetPresetConfig>({});
	let budgetInvalid = $state(false);

	let repeats = $state<number | null>(3);
	let baseSeed = $state<number | null>(null);
	let seedError = $state('');
	let selectedAppIds = $state<Set<number>>(new Set());

	let agentConfig = $state<AgentConfigInfo | null>(null);
	let agentConfigError = $state('');
	let submitting = $state(false);
	let error = $state('');
	let fieldErrors = $state<Record<string, string>>({});

	async function loadAgentConfig() {
		agentConfigError = '';
		try {
			agentConfig = await getAgentConfig();
		} catch (e) {
			agentConfigError = formatApiError(e, 'Could not load the platform defaults.');
		}
	}

	onMount(loadAgentConfig);

	// Pickers auto-select the default preset without an onchange; resolve the
	// selected preset so the override placeholders show what they sit on.
	$effect(() => {
		const id = samplingPresetId;
		if (id == null) {
			samplingPreset = null;
			return;
		}
		if (untrack(() => samplingPreset?.id) === id) return;
		let cancelled = false;
		resolvePreset('sampling', id).then((p) => {
			if (!cancelled) samplingPreset = p;
		});
		return () => {
			cancelled = true;
		};
	});

	$effect(() => {
		const id = budgetPresetId;
		if (id == null) {
			budgetPreset = null;
			return;
		}
		if (untrack(() => budgetPreset?.id) === id) return;
		let cancelled = false;
		resolvePreset('budget', id).then((p) => {
			if (!cancelled) budgetPreset = p;
		});
		return () => {
			cancelled = true;
		};
	});

	const modeItems = [
		{ value: 'agent', label: 'Agent', icon: Bot, badge: 'Recommended', title: 'A sandboxed coding agent builds each app' },
		{ value: 'scaffolding', label: 'Template scaffolding', icon: FileCode, title: 'Template prompts fill a fixed skeleton' },
	];

	// Template scaffolding has no implicit sampling preset: the system default
	// adds nothing there and would only mislabel runs that sample at the mode
	// default (temperature 0.3), so it is sent as "no preset".
	const effectiveSamplingPresetId = $derived(
		mode === 'scaffolding' && samplingPreset?.is_default ? null : samplingPresetId
	);

	// Scaffolding placeholders come from the server (mode default → preset).
	let scaffoldingBase = $state<{ values: LLMParams; sources: Record<string, string> } | null>(null);
	$effect(() => {
		if (mode !== 'scaffolding') return;
		const presetId = effectiveSamplingPresetId;
		const controller = new AbortController();
		previewEffectiveLLMParams({ mode: 'scaffolding', sampling_preset_id: presetId }, { signal: controller.signal })
			.then((result) => (scaffoldingBase = { values: result.effective, sources: result.sources }))
			.catch((e: unknown) => {
				if (!isAbortError(e)) scaffoldingBase = null;
			});
		return () => controller.abort();
	});

	// Placeholders show what the experiment-wide overrides sit on.
	const samplingInherited = $derived.by(() => {
		if (mode === 'scaffolding' && scaffoldingBase) return scaffoldingBase;
		const params = ((samplingPreset?.config as SamplingPresetConfig | undefined)?.llm_params ?? {}) as LLMParams;
		return {
			values: params,
			sources: Object.fromEntries(Object.keys(params).map((key) => [key, 'preset'])),
		};
	});
	const budgetInherited = $derived(
		budgetPreset ? { values: (budgetPreset.config ?? {}) as BudgetPresetConfig, source: 'preset' } : null
	);

	const repeatsProblem = $derived(
		repeats == null
			? 'Required'
			: !Number.isInteger(repeats) || repeats < 1 || repeats > 20
				? 'Between 1 and 20'
				: ''
	);

	function onSeedInput(event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		if (raw === '') {
			seedError = el.validity.badInput ? 'Incomplete number' : '';
			if (!el.validity.badInput) baseSeed = null;
			return;
		}
		const value = Number(raw);
		if (!Number.isInteger(value) || value < 0 || value > MAX_SEED) {
			seedError = `A whole number from 0 to ${MAX_SEED.toLocaleString()}`;
			return;
		}
		seedError = '';
		baseSeed = value;
	}

	function rollSeed() {
		baseSeed = Math.floor(Math.random() * MAX_SEED);
		seedError = '';
	}

	const missing = $derived.by(() => {
		const list: string[] = [];
		if (!name.trim()) list.push('a name');
		if (!slug.trim()) list.push('a slug');
		if (!stackSlug) list.push('a stack');
		if (selectedAppIds.size === 0) list.push('at least one app spec');
		return list;
	});
	const invalid = $derived(
		llmInvalid || (mode === 'agent' && budgetInvalid) || !!repeatsProblem || !!seedError
	);
	const canSubmit = $derived(!submitting && missing.length === 0 && !invalid);

	function experimentLLMDefaults(): LLMParams {
		const params = cleanLLMParams($state.snapshot(llmDefaults) as LLMParams);
		if (mode === 'agent') {
			// The gateway drops these for agent runs; never store them silently.
			delete params.stop;
			delete params.response_format;
		}
		return params;
	}

	async function submit() {
		if (!canSubmit) return;
		submitting = true;
		error = '';
		fieldErrors = {};
		try {
			const experiment = await createExperiment({
				name: name.trim(),
				slug: slug.trim(),
				description,
				hypothesis,
				app_requirement_ids: [...selectedAppIds],
				repeats: repeats ?? 3,
				base_seed: baseSeed,
				mode,
				stack_slug: stackSlug || null,
				sampling_preset_id: effectiveSamplingPresetId,
				budget_preset_id: mode === 'agent' ? budgetPresetId : null,
				agent_config: mode === 'agent' ? ($state.snapshot(budgetOverrides) as BudgetPresetConfig) : {},
				llm_defaults: experimentLLMDefaults(),
			});
			goto(`/experiments/${experiment.id}`);
		} catch (e) {
			fieldErrors = apiFieldErrors(e);
			error = formatApiError(e, 'Failed to create the experiment.');
		} finally {
			submitting = false;
		}
	}
</script>

<div class="mx-auto w-full max-w-3xl space-y-6">
	<PageHeader
		title="New experiment"
		description="A designed comparison: every condition runs on every app, several times, with the same seed per (app, repeat) cell. Conditions are added on the next page."
		back={{ href: '/experiments', label: 'Experiments' }}
	/>

	<Card.Root>
		<Card.Header>
			<Card.Title>Basics</Card.Title>
			<Card.Description>Name the experiment and record what it should show.</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="exp-name">Name <span class="text-destructive" aria-hidden="true">*</span></Label>
					<Input id="exp-name" bind:value={name} required placeholder="Harness comparison on CRUD apps" />
					{#if fieldErrors.name}<p class="text-[11px] text-danger-fg">{fieldErrors.name}</p>{/if}
				</div>
				<div class="space-y-1.5">
					<Label for="exp-slug">Slug <span class="text-destructive" aria-hidden="true">*</span></Label>
					<Input
						id="exp-slug"
						required
						value={slug}
						oninput={(e: Event) => {
							slugTouched = true;
							slugInput = (e.currentTarget as HTMLInputElement).value;
						}}
						placeholder="harness-comparison"
						class="font-mono"
					/>
					{#if fieldErrors.slug}<p class="text-[11px] text-danger-fg">{fieldErrors.slug}</p>{/if}
				</div>
			</div>
			<div class="space-y-1.5">
				<Label for="exp-description">Description</Label>
				<Textarea id="exp-description" bind:value={description} rows={2} />
			</div>
			<div class="space-y-1.5">
				<Label for="exp-hypothesis">Hypothesis (optional)</Label>
				<Textarea
					id="exp-hypothesis"
					bind:value={hypothesis}
					rows={2}
					placeholder="What this experiment is meant to show, for the eventual report."
				/>
			</div>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Method</Card.Title>
			<Card.Description>How apps are generated, and the stack every condition runs on.</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			<div class="space-y-2">
				<Segmented aria-label="Generation method" bind:value={mode} items={modeItems} />
				<p class="text-xs text-muted-foreground">
					{#if mode === 'agent'}
						Each condition is a model × harness preset. Every condition gets the same brief, stack, budget and sampling
						settings, so differences come from the model and the harness.
					{:else}
						Each condition is a model × prompt profile; prompts are rendered from the profile's blocks.
					{/if}
				</p>
			</div>
			<div class="space-y-1.5">
				<StackSelect bind:value={stackSlug} defaultSlug={agentConfig?.default_stack_slug} id="exp-stack" />
				<p class="text-xs text-muted-foreground">
					Experiment-wide. A condition whose profile targets another stack is rejected.
				</p>
				{#if fieldErrors.stack_slug}<p class="text-[11px] text-danger-fg">{fieldErrors.stack_slug}</p>{/if}
			</div>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Model settings</Card.Title>
			<Card.Description>
				Apply identically to every model and condition; per-model support (dropped parameters, output caps) shows in the
				preview.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			<PresetPicker
				kind="sampling"
				bind:selectedId={samplingPresetId}
				idPrefix="exp-sampling"
				onchange={(p) => (samplingPreset = p)}
			/>
			{#if mode === 'scaffolding' && samplingPreset?.is_default}
				<p class="text-[11px] text-muted-foreground">
					Template scaffolding has no default preset: with this one selected, no preset is recorded and runs sample at
					the template-mode defaults shown below.
				</p>
			{/if}
			<div class="space-y-1.5">
				<p class="text-sm font-medium">Experiment overrides</p>
				<LLMParamsEditor
					bind:params={llmDefaults}
					inherited={samplingInherited}
					mode={mode === 'agent' ? 'agent' : 'scaffolding'}
					idPrefix="exp-llm"
					bind:invalid={llmInvalid}
					hint={mode === 'agent'
						? "Blank fields keep the sampling preset's value (or the provider default). Conditions may add their own overrides as a declared ablation."
						: "Blank fields keep the value shown: the sampling preset's, else the template-mode default. A condition's profile LLM defaults sit below the preset; conditions may add their own overrides as a declared ablation."}
				/>
			</div>
		</Card.Content>
	</Card.Root>

	{#if mode === 'agent'}
		<Card.Root>
			<Card.Header>
				<Card.Title>Budget</Card.Title>
				<Card.Description>
					Per-run limits, the same for every condition. The request budget binds; wall clock, tokens and cost are
					safety nets. Runs that hit a limit still count, with their termination recorded.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-4">
				{#if agentConfigError}
					<ErrorAlert message={agentConfigError} onretry={loadAgentConfig} />
				{/if}
				<PresetPicker
					kind="budget"
					bind:selectedId={budgetPresetId}
					idPrefix="exp-budget"
					onchange={(p) => (budgetPreset = p)}
				/>
				<div class="space-y-1.5">
					<p class="text-sm font-medium">Experiment overrides</p>
					<BudgetFields
						bind:values={budgetOverrides}
						config={agentConfig}
						inherited={budgetInherited}
						idPrefix="exp-budget-overrides"
						bind:invalid={budgetInvalid}
					/>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}

	<Card.Root>
		<Card.Header>
			<Card.Title>Repeats &amp; seed</Card.Title>
			<Card.Description>Independent trials per cell; comparisons need more than one.</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="exp-repeats">Repeats per cell</Label>
					<Input
						id="exp-repeats"
						type="number"
						inputmode="numeric"
						min={1}
						max={20}
						step={1}
						value={numberInputValue(repeats)}
						oninput={(e: Event) => {
							const raw = (e.currentTarget as HTMLInputElement).value.trim();
							repeats = raw === '' ? null : Number(raw);
						}}
						aria-invalid={repeatsProblem ? true : undefined}
						aria-describedby="exp-repeats-help"
					/>
					<p id="exp-repeats-help" class="text-xs {repeatsProblem ? 'text-danger-fg' : 'text-muted-foreground'}">
						{repeatsProblem || 'Agent runs vary between trials; 3 or more lets you report a spread.'}
					</p>
				</div>
				<div class="space-y-1.5">
					<Label for="exp-seed">Base seed (optional)</Label>
					<div class="flex gap-2">
						<Input
							id="exp-seed"
							type="number"
							inputmode="numeric"
							min={0}
							max={MAX_SEED}
							step={1}
							value={numberInputValue(baseSeed)}
							placeholder="random per run"
							aria-invalid={seedError ? true : undefined}
							aria-describedby="exp-seed-help"
							class="font-mono"
							oninput={onSeedInput}
						/>
						<Button variant="outline" size="sm" class="h-9 shrink-0" onclick={rollSeed} title="Pick a random base seed">
							<Dices /> Random
						</Button>
					</div>
					<p id="exp-seed-help" class="text-xs {seedError ? 'text-danger-fg' : 'text-muted-foreground'}">
						{seedError || 'Set it for a reproducible seed matrix across launches; every condition shares the seed of an (app, repeat) cell.'}
					</p>
				</div>
			</div>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Apps</Card.Title>
			<Card.Description>The app specs every condition builds; runs are graded against them.</Card.Description>
		</Card.Header>
		<Card.Content>
			<AppSpecPicker bind:selectedIds={selectedAppIds} label="" aria-label="App specs" idPrefix="exp-apps" />
		</Card.Content>
	</Card.Root>

	{#if error}
		<ErrorAlert message={error} />
	{/if}

	<div class="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
		{#if missing.length > 0}
			<p class="text-xs text-muted-foreground sm:mr-auto">Still needed: {missing.join(', ')}.</p>
		{:else if invalid}
			<p class="text-xs text-warning-fg sm:mr-auto">Fix the highlighted fields first.</p>
		{/if}
		<div class="flex justify-end gap-2">
			<Button variant="outline" href="/experiments">Cancel</Button>
			<Button onclick={submit} disabled={!canSubmit}>
				{#if submitting}<LoaderCircle class="h-4 w-4 animate-spin" />{/if}
				Create experiment
			</Button>
		</div>
	</div>
</div>
