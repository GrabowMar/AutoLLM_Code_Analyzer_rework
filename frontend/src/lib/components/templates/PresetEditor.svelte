<script lang="ts" module>
	import type { PresetConfigByKind, PresetKind } from '$lib/api/generation';

	export interface PresetSaveData {
		kind: PresetKind;
		slug: string;
		name: string;
		description: string;
		config: PresetConfigByKind[PresetKind];
	}
</script>

<script lang="ts">
	/**
	 * PresetEditor — create, edit (content change = new version) or duplicate a
	 * run preset. The draft config is checked live against `/presets/validate/`
	 * (debounced, stale requests aborted); the parent performs the save.
	 *
	 * Props: `kind`; `initial` (null = new; with `duplicate` = prefill a new slug);
	 * `harnessName` (new harness preset: preselected harness); `agentConfig`
	 * (`/agent/config/`: harness option schemas, budget limits and defaults);
	 * `takenSlugs` (visible slugs of this kind, for an early collision hint);
	 * `saving`, `error`, `onSave(data, isNew)`, `onCancel`.
	 */
	import { untrack } from 'svelte';
	import {
		validateRunPreset,
		type AgentConfigInfo,
		type BudgetPresetConfig,
		type HarnessOptions,
		type LLMParams,
		type RunPreset,
		type SamplingPolicy,
	} from '$lib/api/generation';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { HarnessOptionsEditor, LLMParamsEditor } from '$lib/components/generator';
	import BudgetFields from './BudgetFields.svelte';
	import {
		SAMPLING_POLICY_LABELS,
		canonicalJson,
		presetSlugFromName,
		presetSlugProblem,
	} from './presets';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import XIcon from '@lucide/svelte/icons/x';

	interface Props {
		kind: PresetKind;
		initial: RunPreset | null;
		duplicate?: boolean;
		harnessName?: string;
		agentConfig: AgentConfigInfo | null;
		takenSlugs: string[];
		saving: boolean;
		error: string;
		onSave: (data: PresetSaveData, isNew: boolean) => void;
		onCancel: () => void;
	}

	let {
		kind,
		initial,
		duplicate = false,
		harnessName = '',
		agentConfig,
		takenSlugs,
		saving,
		error,
		onSave,
		onCancel,
	}: Props = $props();

	// The parent remounts this editor ({#key}) for another preset: snapshot once.
	const seed = untrack(() => initial);
	const isNew = seed === null || untrack(() => duplicate);
	const seedConfig = (seed?.config ?? {}) as Record<string, unknown>;
	const idPrefix = `preset-${untrack(() => kind)}`;

	let name = $state(seed ? (isNew ? `${seed.name} (copy)` : seed.name) : '');
	let slugInput = $state(seed && !isNew ? seed.slug : '');
	let slugTouched = $state(false);
	let description = $state(seed?.description ?? '');
	const slug = $derived(isNew && !slugTouched ? presetSlugFromName(name) : slugInput);

	// harness
	let harnessChoice = $state(
		typeof seedConfig.harness === 'string' ? seedConfig.harness : untrack(() => harnessName)
	);
	let options = $state<HarnessOptions>({ ...((seedConfig.options as HarnessOptions | undefined) ?? {}) });
	let optionsInvalid = $state(false);
	// sampling
	let llmParams = $state<LLMParams>({ ...((seedConfig.llm_params as LLMParams | undefined) ?? {}) });
	let samplingPolicy = $state(typeof seedConfig.sampling_policy === 'string' ? seedConfig.sampling_policy : '');
	let llmInvalid = $state(false);
	// budget
	let budget = $state<BudgetPresetConfig>(untrack(() => kind) === 'budget' ? { ...(seedConfig as BudgetPresetConfig) } : {});
	let budgetInvalid = $state(false);

	const tunableHarnesses = $derived((agentConfig?.harnesses ?? []).filter((h) => h.option_schema.length > 0));
	// A new harness preset defaults to the first harness that has options to change.
	const harness = $derived(harnessChoice || (isNew ? (tunableHarnesses[0]?.name ?? '') : ''));
	const harnessInfo = $derived(agentConfig?.harnesses.find((h) => h.name === harness));

	function pickHarness(name: string) {
		if (name === harness) return;
		harnessChoice = name;
		options = {};
	}

	const config = $derived.by((): PresetConfigByKind[PresetKind] => {
		if (kind === 'harness') return { harness, options };
		if (kind === 'sampling') {
			return samplingPolicy
				? { llm_params: llmParams, sampling_policy: samplingPolicy as SamplingPolicy }
				: { llm_params: llmParams };
		}
		return budget;
	});
	const configKey = $derived(canonicalJson(config));

	const localInvalid = $derived(
		(kind === 'harness' && optionsInvalid) || (kind === 'sampling' && llmInvalid) || (kind === 'budget' && budgetInvalid)
	);
	const allDefaultHarness = $derived(kind === 'harness' && Object.keys(options).length === 0);
	const missingHarness = $derived(kind === 'harness' && !harnessInfo);
	// An empty sampling or budget preset would only duplicate the system default.
	const emptyConfig = $derived(
		(kind === 'sampling' && Object.keys(llmParams).length === 0 && !samplingPolicy) ||
			(kind === 'budget' && Object.keys(budget).length === 0)
	);

	// ── live validation ────────────────────────────────────────────────
	let check = $state<{ key: string; ok: boolean; errors: string[]; normalized: unknown; failed: string } | null>(null);
	let checking = $state(false);

	$effect(() => {
		const key = configKey;
		const k = kind;
		if (localInvalid || allDefaultHarness || missingHarness || emptyConfig) {
			checking = false;
			return;
		}
		const body = $state.snapshot(config) as PresetConfigByKind[PresetKind];
		const controller = new AbortController();
		checking = true;
		const timer = setTimeout(() => {
			validateRunPreset(k, body, { signal: controller.signal })
				.then((result) => {
					check = { key, ok: result.ok, errors: result.errors, normalized: result.config, failed: '' };
				})
				.catch((e: unknown) => {
					if (isAbortError(e)) return;
					// The save validates again; a failed check only loses the early hint.
					check = { key, ok: true, errors: [], normalized: null, failed: formatApiError(e, 'Could not check the config.') };
				})
				.finally(() => {
					if (!controller.signal.aborted) checking = false;
				});
		}, 400);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	const current = $derived(check && check.key === configKey ? check : null);
	const serverErrors = $derived(current && !current.ok ? current.errors : []);
	const storedConfig = $derived(current?.normalized ?? config);

	// ── save state ─────────────────────────────────────────────────────
	const slugProblem = $derived(isNew ? presetSlugProblem(slug, takenSlugs) : '');
	const contentChanged = $derived(isNew || canonicalJson(storedConfig) !== canonicalJson(seed?.config ?? {}));
	const metaChanged = $derived(!!seed && (name.trim() !== seed.name || description.trim() !== (seed.description ?? '')));
	const canSave = $derived(
		!saving &&
			!!name.trim() &&
			!slugProblem &&
			!localInvalid &&
			!allDefaultHarness &&
			!missingHarness &&
			!emptyConfig &&
			serverErrors.length === 0 &&
			(isNew || contentChanged || metaChanged)
	);
	const saveLabel = $derived(
		isNew ? 'Create preset' : contentChanged ? `Save as v${(seed?.latest_version ?? seed?.version ?? 0) + 1}` : 'Save changes'
	);

	let showStored = $state(false);

	function submit() {
		if (!canSave) return;
		onSave(
			{
				kind,
				slug,
				name: name.trim(),
				description: description.trim(),
				config: $state.snapshot(config) as PresetConfigByKind[PresetKind],
			},
			isNew
		);
	}

	const KIND_COPY: Record<PresetKind, string> = {
		harness:
			'A harness and the options it runs with. Every model gets the same harness configuration; only per-model fields (model slug, context window, output cap) are derived.',
		sampling:
			'Model-agnostic sampling settings, applied identically to every model in a batch. A model that does not support a parameter drops it; the run preview shows this per model.',
		budget:
			'Per-run limits and network access. The request budget is the limit that should bind; wall clock, tokens and cost are safety nets.',
	};
	const NAME_PLACEHOLDERS: Record<PresetKind, string> = {
		harness: 'e.g. Aider whole-file edits',
		sampling: 'e.g. Temperature 0',
		budget: 'e.g. Quick smoke test',
	};
	const samplingItems = Object.entries(SAMPLING_POLICY_LABELS).map(([value, label]) => ({ value, label }));
	const platformPolicy = $derived(agentConfig?.defaults?.sampling_policy ?? 'provider_default');
</script>

<Card.Root>
	<Card.Header class="border-b">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0 space-y-1">
				<Card.Title>
					{#if seed === null}New {kind} preset{:else if duplicate}Duplicate {seed.name}{:else}Edit {seed.name}{/if}
				</Card.Title>
				<Card.Description>{KIND_COPY[kind]}</Card.Description>
			</div>
			<Button variant="ghost" size="icon" class="size-8 shrink-0" aria-label="Close editor" onclick={onCancel}>
				<XIcon />
			</Button>
		</div>
	</Card.Header>
	<Card.Content class="space-y-5">
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label class="text-xs" for="{idPrefix}-name">Name</Label>
				<Input id="{idPrefix}-name" bind:value={name} maxlength={200} placeholder={NAME_PLACEHOLDERS[kind]} class="h-9 text-sm" />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs" for="{idPrefix}-slug">Slug</Label>
				<Input
					id="{idPrefix}-slug"
					value={slug}
					disabled={!isNew}
					oninput={(e: Event) => {
						slugTouched = true;
						slugInput = (e.currentTarget as HTMLInputElement).value;
					}}
					aria-invalid={isNew && slug && slugProblem ? true : undefined}
					aria-describedby="{idPrefix}-slug-note"
					class="h-9 text-sm font-mono"
				/>
				<p id="{idPrefix}-slug-note" class={cn('text-[11px]', isNew && slug && slugProblem ? 'text-danger-fg' : 'text-muted-foreground')}>
					{#if !isNew}Slugs are permanent; every version keeps this one.{:else if slug && slugProblem}{slugProblem}{:else}Global per kind; recorded in each job's provenance.{/if}
				</p>
			</div>
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs" for="{idPrefix}-description">Description</Label>
			<Textarea id="{idPrefix}-description" bind:value={description} rows={2} class="text-sm" placeholder="What this preset changes, and why" />
		</div>

		{#if kind === 'harness'}
			<div class="space-y-3">
				{#if !agentConfig}
					<ErrorAlert message="The harness catalog is not loaded, so options cannot be edited. Reload the Library and try again." />
				{:else if tunableHarnesses.length === 0}
					<p class="rounded-md border border-border px-3 py-2 text-sm text-muted-foreground">
						No harness has tunable options in this deployment, so every harness preset would equal its system default.
					</p>
				{:else}
					<div class="space-y-1.5">
						<Label class="text-xs" for="{idPrefix}-harness">Harness</Label>
						<Select.Simple
							id="{idPrefix}-harness"
							class="sm:max-w-sm"
							value={harness}
							items={tunableHarnesses.map((h) => ({ value: h.name, label: `${h.label} ${h.version}`, description: h.description }))}
							disabled={!isNew}
							onValueChange={pickHarness}
						/>
						<p class="text-[11px] text-muted-foreground">
							{#if !isNew}A preset keeps its harness; duplicate it to configure another one.{:else if harnessInfo}{harnessInfo.description}{/if}
						</p>
					</div>
					{#if harnessInfo}
						<HarnessOptionsEditor
							schema={harnessInfo.option_schema}
							bind:options
							defaults={harnessInfo.default_options}
							idPrefix="{idPrefix}-options"
							bind:invalid={optionsInvalid}
						/>
						{#if allDefaultHarness}
							<p class="flex items-start gap-1.5 text-[11px] text-warning-fg" role="status">
								<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
								Every option is at its default, which is the system preset {harnessInfo.name}-default. Change at least one option.
							</p>
						{/if}
					{:else if harness}
						<ErrorAlert message="Harness “{harness}” is not available in this deployment." />
					{/if}
				{/if}
			</div>
		{:else if kind === 'sampling'}
			<div class="space-y-4">
				<LLMParamsEditor bind:params={llmParams} mode="preset" idPrefix="{idPrefix}-llm" bind:invalid={llmInvalid} />
				<div class="space-y-1.5">
					<Label class="text-xs" for="{idPrefix}-policy">Harness sampling</Label>
					<Select.Simple
						id="{idPrefix}-policy"
						size="sm"
						class={cn('sm:max-w-md', samplingPolicy && 'border-primary/50')}
						value={samplingPolicy}
						items={samplingItems}
						placeholder="Platform default — {SAMPLING_POLICY_LABELS[platformPolicy] ?? platformPolicy}"
						clearLabel="Platform default — {SAMPLING_POLICY_LABELS[platformPolicy] ?? platformPolicy}"
						aria-describedby="{idPrefix}-policy-note"
						onValueChange={(v) => (samplingPolicy = v)}
					/>
					<p
						id="{idPrefix}-policy-note"
						class={cn('text-[11px]', samplingPolicy === 'harness_native' ? 'text-warning-fg' : 'text-muted-foreground')}
					>
						{#if samplingPolicy === 'harness_native'}
							Each harness then sends its own sampling settings, so harnesses are no longer compared under identical
							sampling. Agent runs only.
						{:else}
							Agent runs only: whether sampling settings a harness sends on its own are stripped (provider defaults)
							or passed through.
						{/if}
					</p>
				</div>
			</div>
		{:else}
			<div class="space-y-2">
				<BudgetFields bind:values={budget} config={agentConfig} idPrefix="{idPrefix}-budget" bind:invalid={budgetInvalid} />
				<p class="text-[11px] text-muted-foreground">
					Blank fields keep the platform default. The limits are written into the agent brief, so runs under a
					non-default budget form their own comparison group. A tight cost or wall-clock cap gives expensive or
					slow models fewer steps than the request budget allows.
				</p>
			</div>
		{/if}

		<div class="space-y-2 rounded-md border border-border bg-muted/20 p-3" aria-live="polite">
			<div class="flex flex-wrap items-center gap-2 text-xs">
				{#if localInvalid}
					<TriangleAlert class="size-3.5 text-warning-fg" aria-hidden="true" />
					<span class="text-warning-fg">Fix the highlighted fields first.</span>
				{:else if emptyConfig}
					<span class="text-muted-foreground">
						Nothing set yet: an empty preset equals the system default ({kind === 'sampling'
							? 'provider-default'
							: 'platform-default'}). Set at least one value.
					</span>
				{:else if allDefaultHarness || missingHarness}
					<span class="text-muted-foreground">Nothing to check yet.</span>
				{:else if checking || !current}
					<LoaderCircle class="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden="true" />
					<span class="text-muted-foreground">Checking the config…</span>
				{:else if serverErrors.length > 0}
					<TriangleAlert class="size-3.5 text-danger-fg" aria-hidden="true" />
					<span class="text-danger-fg">The server rejects this config.</span>
				{:else if current.failed}
					<TriangleAlert class="size-3.5 text-warning-fg" aria-hidden="true" />
					<span class="text-muted-foreground">{current.failed} It is checked again on save.</span>
				{:else}
					<CircleCheck class="size-3.5 text-success-fg" aria-hidden="true" />
					<span class="text-muted-foreground">Valid.</span>
				{/if}
				<button
					type="button"
					class="ml-auto flex cursor-pointer items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
					aria-expanded={showStored}
					aria-controls="{idPrefix}-stored"
					onclick={() => (showStored = !showStored)}
				>
					<ChevronRight class={cn('size-3.5 transition-transform duration-150', showStored && 'rotate-90')} aria-hidden="true" />
					Stored config
				</button>
			</div>
			{#if serverErrors.length > 0}
				<ul class="list-disc space-y-0.5 pl-5 text-xs text-danger-fg">
					{#each serverErrors as message, i (i)}<li>{message}</li>{/each}
				</ul>
			{/if}
			{#if showStored}
				<pre id="{idPrefix}-stored" class="terminal max-h-48 whitespace-pre-wrap break-words text-[11px]">{JSON.stringify(storedConfig, null, 2)}</pre>
			{/if}
		</div>

		{#if error}
			<ErrorAlert message={error} />
		{/if}

		<div class="flex flex-col-reverse gap-2 border-t pt-4 sm:flex-row sm:items-center sm:justify-end">
			{#if seed && !isNew}
				<p class="text-[11px] text-muted-foreground sm:mr-auto">
					{#if contentChanged}
						Saving creates v{(seed.latest_version ?? seed.version) + 1}; jobs that used v{seed.version} keep it.
					{:else if metaChanged}
						Only the name or description changed: v{seed.version} is updated, its content hash stays.
					{:else}
						No changes yet.
					{/if}
				</p>
			{/if}
			<div class="flex justify-end gap-2">
				<Button variant="outline" size="sm" onclick={onCancel} disabled={saving}>Cancel</Button>
				<Button size="sm" onclick={submit} disabled={!canSave}>
					{#if saving}<LoaderCircle class="animate-spin" />{/if}
					{saveLabel}
				</Button>
			</div>
		</div>
	</Card.Content>
</Card.Root>
