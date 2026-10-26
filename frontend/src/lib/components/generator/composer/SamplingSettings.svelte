<script lang="ts" module>
	import type { LLMParams, RunPreset, SamplingPresetConfig } from '$lib/api/generation';

	/** Not sampling properties: presets reject them, so they stay inline overrides. */
	export const NON_PRESET_LLM_KEYS = ['provider', 'stop', 'response_format'] as const;

	export function presetableParams(params: LLMParams): LLMParams {
		const out: Record<string, unknown> = { ...params };
		for (const key of NON_PRESET_LLM_KEYS) delete out[key];
		return out as LLMParams;
	}

	/**
	 * A system preset that sets no sampling parameter (the agent default,
	 * "Provider defaults"). Template modes sample at their mode default without
	 * any preset, so there it would only mislabel the runs' provenance.
	 */
	export function isNoopSamplingPreset(preset: RunPreset | null | undefined): boolean {
		if (!preset || preset.kind !== 'sampling' || !preset.is_system) return false;
		const config = preset.config as SamplingPresetConfig;
		return Object.keys(config.llm_params ?? {}).length === 0 && !config.sampling_policy;
	}

	export function inlineOnlyParams(params: LLMParams): LLMParams {
		const out: Record<string, unknown> = {};
		for (const key of NON_PRESET_LLM_KEYS) if (params[key] != null) out[key] = params[key];
		return out as LLMParams;
	}
</script>

<script lang="ts">
	/**
	 * SamplingSettings — the composer's "Model settings": a sampling preset,
	 * inline overrides on top (LLMParamsEditor), and "Save as preset…" which
	 * folds the overrides into a new preset and selects it.
	 *
	 * Props:
	 * - `mode`: 'agent' | 'scaffolding' | 'custom' (LLMParamsEditor mode).
	 * - `presetId` (bindable), `pinned` (exact version from a job), `preset` (the
	 *   resolved selected preset, for inherited values and "Save as preset…").
	 * - `params` (bindable sparse inline overrides), `inherited` (layers below).
	 * - agent only: `policy` (bindable inline sampling_policy override), `inheritedPolicy`.
	 * - `errors` (llm field errors), `invalid` (bindable output), `disabled`, `onchange()`.
	 * - `hideProviderRouting`, `localModels`: passed to LLMParamsEditor (no OpenRouter
	 *   model selected / some models run on local endpoints).
	 */
	import type { PresetRef, SamplingPolicy } from '$lib/api/generation';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import LLMParamsEditor, { type InheritedLLMParams } from '../LLMParamsEditor.svelte';
	import PresetPicker from '../PresetPicker.svelte';
	import { cleanLLMParams } from '../llm-params';
	import SavePresetDialog from './SavePresetDialog.svelte';
	import Save from '@lucide/svelte/icons/save';

	interface Props {
		mode: 'agent' | 'scaffolding' | 'custom';
		presetId?: number | null;
		pinned?: PresetRef | null;
		preset?: RunPreset<'sampling'> | null;
		params?: LLMParams;
		inherited?: InheritedLLMParams;
		policy?: SamplingPolicy;
		inheritedPolicy?: { value: string; source: string } | null;
		errors?: Record<string, string>;
		invalid?: boolean;
		disabled?: boolean;
		hideProviderRouting?: boolean;
		localModels?: boolean;
		/** Replaces the mode's inheritance note (e.g. when the section already says it). */
		hint?: string;
		onchange?: () => void;
	}

	let {
		mode,
		presetId = $bindable(null),
		pinned = null,
		preset = null,
		params = $bindable({}),
		inherited,
		policy = $bindable(undefined),
		inheritedPolicy = null,
		errors = {},
		invalid = $bindable(false),
		disabled = false,
		hideProviderRouting = false,
		localModels = false,
		hint: hintProp,
		onchange,
	}: Props = $props();

	const POLICY_LABELS: Record<string, string> = {
		provider_default: 'Provider defaults',
		harness_native: 'Harness-native',
	};
	const policyItems = [
		{
			value: 'provider_default',
			label: POLICY_LABELS.provider_default,
			description: 'The gateway strips sampling knobs a harness sends on its own.',
		},
		{
			value: 'harness_native',
			label: POLICY_LABELS.harness_native,
			description: 'Each harness’s own sampling reaches the model (harnesses then differ).',
		},
	];

	let saveOpen = $state(false);

	// Template modes: no preset means the mode default (temperature 0.3, output
	// cap 32,000); the no-op "Provider defaults" preset is not offered there.
	const templateMode = $derived(mode !== 'agent');
	const noneOption = $derived(
		templateMode ? { label: 'No preset', description: 'Mode default: temperature 0.3 · output cap 32,000' } : null
	);
	$effect(() => {
		if (templateMode && isNoopSamplingPreset(preset)) presetId = null;
	});

	const savable = $derived(presetableParams(cleanLLMParams(params)));
	const canSave = $derived(Object.keys(savable).length > 0 || (mode === 'agent' && !!policy));
	const saveConfig = $derived.by((): SamplingPresetConfig => {
		const base = preset?.config;
		const config: SamplingPresetConfig = { llm_params: { ...(base?.llm_params ?? {}), ...savable } };
		const nextPolicy = (mode === 'agent' ? policy : undefined) ?? base?.sampling_policy;
		if (nextPolicy) config.sampling_policy = nextPolicy;
		return config;
	});

	const POLICY_SOURCES: Record<string, string> = { default: 'platform default', preset: 'preset' };
	const inheritedPolicyText = $derived.by(() => {
		if (!inheritedPolicy) return 'platform default';
		const label = POLICY_LABELS[inheritedPolicy.value] ?? inheritedPolicy.value;
		const source = POLICY_SOURCES[inheritedPolicy.source] ?? inheritedPolicy.source;
		return source ? `${label} · ${source}` : label;
	});
	// The trigger is narrow: the platform default is implied, a preset is named.
	const policyPlaceholder = $derived.by(() => {
		if (!inheritedPolicy) return 'Platform default';
		const label = POLICY_LABELS[inheritedPolicy.value] ?? inheritedPolicy.value;
		return inheritedPolicy.source === 'preset' ? `${label} · preset` : label;
	});

	const HINTS = {
		agent:
			'Applies identically to every model; per-model support (dropped parameters, output caps) is in the Run summary. Blank fields inherit the value shown in grey.',
		scaffolding:
			'Applies identically to every model. Blank fields inherit the value shown in grey: the mode default (temperature 0.3, output cap 32,000), then the profile’s LLM defaults, then the sampling preset.',
		custom:
			'Blank fields inherit the value shown in grey: the mode default (temperature 0.3, output cap 32,000), then the sampling preset.',
	};
	const hint = $derived(hintProp ?? HINTS[mode]);

	function onSaved(saved: RunPreset) {
		presetId = saved.id;
		// The overrides now live in the preset; only non-preset keys stay inline.
		params = inlineOnlyParams(cleanLLMParams(params));
		if (mode === 'agent') policy = undefined;
		onchange?.();
	}
</script>

<div class="space-y-4">
	<div class="grid items-start gap-3 {mode === 'agent' ? '@xl:grid-cols-[minmax(0,1fr)_minmax(0,16rem)]' : ''}">
		<PresetPicker
			kind="sampling"
			bind:selectedId={presetId}
			{pinned}
			autoSelectDefault={!templateMode}
			{noneOption}
			filter={templateMode ? (p) => !isNoopSamplingPreset(p) : undefined}
			idPrefix="{mode}-sampling-preset"
			{disabled}
			onchange={() => onchange?.()}
		/>
		{#if mode === 'agent'}
			<div class="min-w-0 space-y-1.5">
				<!-- Same row height as the preset picker's label + Manage link, so the triggers line up. -->
				<div class="flex min-h-4 items-center"><Label for="{mode}-sampling-policy">Sampling policy</Label></div>
				<Select.Simple
					id="{mode}-sampling-policy"
					value={policy ?? ''}
					items={policyItems}
					placeholder={policyPlaceholder}
					clearLabel="Inherit ({inheritedPolicyText})"
					class={policy ? 'border-primary/50' : ''}
					{disabled}
					onValueChange={(v) => {
						policy = (v || undefined) as SamplingPolicy | undefined;
						onchange?.();
					}}
				/>
			</div>
		{/if}
	</div>

	<LLMParamsEditor
		bind:params
		bind:invalid
		{inherited}
		{mode}
		idPrefix="{mode}-llm"
		{errors}
		{hint}
		{disabled}
		{hideProviderRouting}
		{localModels}
		onchange={() => onchange?.()}
	/>

	<div class="flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3">
		<p class="text-[11px] text-muted-foreground">
			{canSave
				? 'Reuse these settings in other batches and experiments as a versioned preset.'
				: 'Set an override to save the combination as a new preset.'}
		</p>
		<Button variant="outline" size="sm" disabled={disabled || !canSave || invalid} onclick={() => (saveOpen = true)}>
			<Save />Save as preset…
		</Button>
	</div>
</div>

<SavePresetDialog bind:open={saveOpen} kind="sampling" config={saveConfig} basedOn={preset} onsaved={onSaved} />
