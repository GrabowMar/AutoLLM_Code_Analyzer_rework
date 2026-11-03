<script lang="ts">
	/**
	 * SamplingPresetEditor — `SamplingContent`: provider defaults or explicit parameters (temperature,
	 * top_p, top_k, min_p, frequency and presence penalties; empty = not sent) and whether the run seed is
	 * forwarded. Sampling applies identically to every model; slot studies refuse provider defaults and
	 * temperature 0 (lint `slot_sampling_provider_default`).
	 *
	 * Props: `value`, `onchange`, `readonly`.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { SamplingContent, SamplingParams } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import NumberInput from './NumberInput.svelte';

	interface Props {
		value: SamplingContent;
		onchange: (v: SamplingContent) => void;
		readonly?: boolean;
	}

	let { value, onchange, readonly = false }: Props = $props();

	const PARAMS: { key: keyof SamplingParams; label: string; min?: number; max?: number; step: number; integer?: boolean; help: string }[] = [
		{ key: 'temperature', label: 'Temperature', min: 0, step: 0.05, help: '≥ 0' },
		{ key: 'top_p', label: 'Top p', min: 0.01, max: 1, step: 0.01, help: '(0, 1]' },
		{ key: 'top_k', label: 'Top k', min: 1, step: 1, integer: true, help: '≥ 1' },
		{ key: 'min_p', label: 'Min p', min: 0, max: 1, step: 0.01, help: '[0, 1]' },
		{ key: 'frequency_penalty', label: 'Frequency penalty', step: 0.1, help: 'provider range' },
		{ key: 'presence_penalty', label: 'Presence penalty', step: 0.1, help: 'provider range' },
	];

	const set = (patch: Partial<SamplingContent>) => onchange({ ...value, ...patch });
	const setParam = (key: keyof SamplingParams, n: number | null) => set({ params: { ...value.params, [key]: n } });
</script>

<div class="space-y-5">
	<FormRow label="Policy" inline help="Provider defaults sends no sampling parameters; explicit forces the values below on every call.">
		<Segmented aria-label="Sampling policy" value={value.policy} disabled={readonly} items={[{ value: 'provider_default', label: 'Provider defaults' }, { value: 'explicit', label: 'Explicit' }]} onValueChange={(v) => set({ policy: v as SamplingContent['policy'] })} />
	</FormRow>
	{#if value.policy === 'explicit'}
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each PARAMS as p (p.key)}
				<FormRow label={p.label} help={p.help}>
					<NumberInput value={value.params[p.key]} min={p.min} max={p.max} step={p.step} placeholder="not sent" {readonly} onchange={(n) => setParam(p.key, n === null ? null : p.integer ? Math.round(n) : n)} />
				</FormRow>
			{/each}
		</div>
		{#if value.params.temperature === 0}
			<p class="text-xs text-warning-fg">Temperature 0 makes repeats near-identical; slot studies refuse it.</p>
		{/if}
	{/if}
	<label class="flex items-start gap-2 text-sm">
		<Checkbox checked={value.forward_seed} disabled={readonly} onCheckedChange={(c) => set({ forward_seed: c === true })} class="mt-0.5" />
		<span>Forward the run seed <span class="block text-xs text-muted-foreground">Sent as the provider's seed where supported; paired comparisons use it.</span></span>
	</label>
</div>
