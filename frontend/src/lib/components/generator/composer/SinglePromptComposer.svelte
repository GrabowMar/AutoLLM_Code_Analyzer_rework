<script lang="ts">
	/**
	 * SinglePromptComposer — one raw system + user prompt to one model (no
	 * template, no grading). The summary shows the effective sampling params
	 * (`/jobs/llm-preview/`); success opens the job page.
	 *
	 * Props: `initial` (draft), `pins` (sampling preset version from a job),
	 * `keyState` (OpenRouter key status: a missing key blocks the start).
	 */
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { createCustomJob, type LLMParams } from '$lib/api/generation';
	import { apiFieldErrors, formatApiError } from '$lib/api/core';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import ModelPicker from '../ModelPicker.svelte';
	import type { OpenRouterKeyState } from '../OpenRouterKeyBanner.svelte';
	import { cleanLLMParams } from '../llm-params';
	import ComposerSection from './ComposerSection.svelte';
	import NumberField from './NumberField.svelte';
	import SamplingSettings from './SamplingSettings.svelte';
	import SimpleSummaryPanel from './SimpleSummaryPanel.svelte';
	import StartBar from './StartBar.svelte';
	import { DEFAULT_SYSTEM_PROMPT, MAX_SEED, randomSeed, saveDraft, type CustomDraft, type PresetPins } from './draft';
	import type { ChecklistItem, IssueAction, SummaryIssue } from './issues';
	import { trackLLMPreview } from './llm-preview.svelte';
	import { trackPresets } from './preset-lookup.svelte';
	import Dices from '@lucide/svelte/icons/dices';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';

	interface Props {
		initial: CustomDraft;
		pins: PresetPins;
		keyState: OpenRouterKeyState;
	}

	let { initial, pins, keyState }: Props = $props();

	const init = untrack(() => initial);
	let systemPrompt = $state(init.systemPrompt);
	let userPrompt = $state(init.userPrompt);
	let modelId = $state<number | null>(init.modelId);
	let samplingPresetId = $state<number | null>(init.samplingPresetId);
	let llmParams = $state<LLMParams>(init.llmParams);
	let seed = $state<number | null>(init.seed);
	let llmInvalid = $state(false);
	let seedInvalid = $state(false);

	const samplingPresets = trackPresets('sampling', () => [samplingPresetId]);
	const samplingPreset = $derived(samplingPresets.find(samplingPresetId) ?? null);
	$effect(() => {
		// A draft's preset that no longer exists: back to the mode default (no preset).
		if (samplingPresetId != null && samplingPresets.isMissing(samplingPresetId)) samplingPresetId = null;
	});

	const llm = trackLLMPreview(() => ({
		mode: 'custom',
		sampling_preset_id: samplingPresetId,
		llm_params: cleanLLMParams(llmParams),
	}));
	const inheritedLLM = $derived.by(() => {
		const result = llm.result;
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		for (const [key, value] of Object.entries(result?.effective ?? {})) {
			const source = result?.sources[key];
			if (value == null || source === 'inline') continue;
			values[key] = value;
			if (source) sources[key] = source;
		}
		return { values: values as LLMParams, sources };
	});

	const ready = $derived(userPrompt.trim().length > 0 && modelId != null);
	const localInvalid = $derived(llmInvalid || seedInvalid);

	const issues = $derived.by((): SummaryIssue[] => {
		const out: SummaryIssue[] = [];
		if (keyState === 'missing') {
			out.push({ code: 'missing_api_key', severity: 'error', detail: 'The run calls the model with your OpenRouter key and none is configured.' });
		} else if (keyState === 'invalid') {
			out.push({ code: 'missing_api_key', severity: 'warn', detail: 'Your OpenRouter key was rejected at its last validation; the run may fail.' });
		}
		out.push({
			code: 'ungraded_task',
			severity: 'info',
			detail: 'A single prompt is not graded: the output is stored as-is, with no build or conformance check.',
		});
		return out;
	});
	const errorCount = $derived(issues.filter((i) => i.severity === 'error').length);
	const actions: Record<string, IssueAction> = {
		missing_api_key: { label: 'Open Settings', href: '/users/settings#credentials' },
	};

	let submitting = $state(false);
	let submitError = $state('');
	let serverFieldErrors = $state<Record<string, string>>({});

	const canStart = $derived(ready && !localInvalid && errorCount === 0 && !submitting);
	const blockedReason = $derived.by(() => {
		if (!userPrompt.trim()) return 'Write the user prompt.';
		if (modelId == null) return 'Pick a model.';
		if (localInvalid) return 'Fix the highlighted fields.';
		if (errorCount > 0) return 'Resolve the errors in the Run summary.';
		return '';
	});

	async function start() {
		if (!canStart || modelId == null) return;
		submitting = true;
		submitError = '';
		serverFieldErrors = {};
		try {
			const llmOverrides = cleanLLMParams(llmParams);
			const job = await createCustomJob({
				model_id: modelId,
				system_prompt: systemPrompt,
				user_prompt: userPrompt,
				sampling_preset_id: samplingPresetId,
				...(Object.keys(llmOverrides).length > 0 ? { llm_params: llmOverrides } : {}),
				seed,
			});
			toast.success('Generation started');
			await goto(`/applications/${job.id}`);
		} catch (e) {
			submitError = formatApiError(e, 'Could not start the generation.');
			serverFieldErrors = apiFieldErrors(e);
		} finally {
			submitting = false;
		}
	}

	const llmErrors = $derived(
		Object.fromEntries(
			Object.entries(serverFieldErrors)
				.filter(([key]) => key.startsWith('llm_params.'))
				.map(([key, message]) => [key.slice('llm_params.'.length), message])
		)
	);

	const checklist = $derived<ChecklistItem[]>([
		{ label: 'Write the user prompt', done: userPrompt.trim().length > 0, href: '#custom-prompt' },
		{ label: 'Pick a model', done: modelId != null, href: '#custom-model' },
	]);

	$effect(() => {
		saveDraft('custom', {
			systemPrompt,
			userPrompt,
			modelId,
			samplingPresetId,
			llmParams: cleanLLMParams($state.snapshot(llmParams) as LLMParams),
			seed,
		});
	});
</script>

<div class="@container/composer">
	<div class="grid items-start gap-4 @4xl/composer:grid-cols-[minmax(0,1fr)_22rem] @6xl/composer:grid-cols-[minmax(0,1fr)_26rem]">
		<div class="min-w-0 space-y-4">
			<ComposerSection step={1} id="custom-prompt" title="Prompt" description="Sent verbatim: no template, stack or brief is added.">
				<div class="space-y-1.5">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<Label for="custom-system">System prompt</Label>
						{#if systemPrompt !== DEFAULT_SYSTEM_PROMPT}
							<Button variant="ghost" size="sm" class="h-7 px-2" onclick={() => (systemPrompt = DEFAULT_SYSTEM_PROMPT)}>
								<RotateCcw />Default
							</Button>
						{/if}
					</div>
					<Textarea id="custom-system" rows={3} bind:value={systemPrompt} class="min-h-0" />
				</div>
				<div class="space-y-1.5">
					<Label for="custom-user">User prompt</Label>
					<Textarea
						id="custom-user"
						rows={8}
						bind:value={userPrompt}
						placeholder="Describe exactly what the model should produce…"
					/>
				</div>
			</ComposerSection>

			<ComposerSection step={2} id="custom-model" title="Model">
				<ModelPicker mode="single" bind:selectedId={modelId} idPrefix="custom-model-picker" label="" aria-label="Model" maxHeight="max-h-96" />
			</ComposerSection>

			<ComposerSection
				step={3}
				id="custom-sampling"
				title="Model settings"
				description="Sampling preset, overrides on top, and an optional seed."
			>
				<SamplingSettings
					mode="custom"
					bind:presetId={samplingPresetId}
					pinned={pins.sampling}
					preset={samplingPreset}
					bind:params={llmParams}
					inherited={inheritedLLM}
					errors={llmErrors}
					bind:invalid={llmInvalid}
				/>
				<div class="border-t border-border/60 pt-4">
					<NumberField
						id="custom-seed"
						label="Seed"
						class="sm:max-w-xs"
						bind:value={seed}
						min={0}
						max={MAX_SEED}
						placeholder="none"
						bind:invalid={seedInvalid}
						error={serverFieldErrors.seed}
						hint="Forwarded to models that accept one; blank sends none."
					>
						<Button
							variant="outline"
							size="icon"
							class="size-8 shrink-0"
							aria-label="Pick a random seed"
							title="Random seed"
							onclick={() => (seed = randomSeed())}
						>
							<Dices />
						</Button>
					</NumberField>
				</div>
			</ComposerSection>
		</div>

		<aside
			class="min-w-0 @4xl/composer:sticky @4xl/composer:top-[4.5rem] @4xl/composer:max-h-[calc(100dvh-5.5rem)] @4xl/composer:overflow-y-auto @4xl/composer:overscroll-contain"
			aria-label="Run summary"
		>
			<SimpleSummaryPanel
				{checklist}
				{ready}
				{llm}
				llmNote="Base layer: the mode default (temperature 0.3, output cap 32,000), then the sampling preset and your overrides."
				{issues}
				{actions}
				{canStart}
				{blockedReason}
				{submitting}
				{submitError}
				startLabel="Start generation"
				onstart={start}
			>
				<div class="rounded-md border border-border bg-surface-2 p-3 text-xs">
					<p class="flex items-baseline gap-2">
						<span class="font-display text-2xl font-semibold tabular-nums">1</span>
						<span class="text-sm text-muted-foreground">run</span>
					</p>
					<p class="text-muted-foreground">
						{seed != null ? `Seed ${seed}` : 'No seed'} · opens the job page when it starts
					</p>
				</div>
			</SimpleSummaryPanel>
		</aside>
	</div>

	<StartBar
		runs={ready ? 1 : null}
		errors={errorCount}
		label="Start generation"
		{canStart}
		busy={submitting}
		reason={blockedReason}
		summaryHref="#run-summary"
		onstart={start}
	/>
</div>
