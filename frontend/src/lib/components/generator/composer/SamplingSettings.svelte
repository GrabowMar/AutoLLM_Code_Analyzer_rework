<script lang="ts" module>
	import type { LLMParams } from '$lib/api/generation';

	/** Not sampling properties: presets reject them, so they stay inline overrides. */
	export const NON_PRESET_LLM_KEYS = ['provider', 'stop', 'response_format'] as const;

	export function presetableParams(params: LLMParams): LLMParams {
		const out: Record<string, unknown> = { ...params };
		for (const key of NON_PRESET_LLM_KEYS) delete out[key];
		return out as LLMParams;
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
	 */
	import type { PresetRef, RunPreset, SamplingPolicy, SamplingPresetConfig } from '$lib/api/generation';
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

	const HINTS = {
		agent:
			'Applies identically to every model; per-model support (dropped parameters, output caps) is in the Run summary. Blank fields inherit the value shown in grey.',
		scaffolding:
			'Applies identically to every model. Blank fields inherit the value shown in grey: the mode default (temperature 0.3, output cap 32,000), then the profile’s LLM defaults, then the sampling preset.',
		custom:
			'Blank fields inherit the value shown in grey: the mode default (temperature 0.3, output cap 32,000), then the sampling preset.',
	};
	const hint = $derived(HINTS[mode]);

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
			idPrefix="{mode}-sampling-preset"
			{disabled}
			onchange={() => onchange?.()}
		/>
		{#if mode === 'agent'}
			<div class="min-w-0 space-y-1.5">
				<Label for="{mode}-sampling-policy">Sampling policy</Label>
				<Select.Simple
					id="{mode}-sampling-policy"
					value={policy ?? ''}
					items={policyItems}
					placeholder={inheritedPolicyText}
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
