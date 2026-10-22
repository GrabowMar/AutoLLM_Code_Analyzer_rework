<script lang="ts">
	/**
	 * BudgetFields — sparse inline overrides of an agent run's budget
	 * (requests, wall clock, tokens, cost cap, network). Blank = inherit; the
	 * placeholder shows the inherited value and where it comes from.
	 *
	 * Props:
	 * - `value` (bindable `AgentConfigOverrides`): only the keys set here
	 *   (other keys such as `sampling_policy` pass through untouched).
	 * - `inherited`: `{ values, sources }` of the layers below (platform defaults,
	 *   budget preset); sources are "default" | "preset".
	 * - `limits`: per-key `{min, max}` from `GET /agent/config/`.
	 * - `networkPolicies`, `idPrefix`, `errors` (keyed by config key), `disabled`.
	 * - `invalid` (bindable output): a typed value was not applied.
	 */
	import type { AgentConfigOverrides, AgentLimit } from '$lib/api/generation';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import NumberField from './NumberField.svelte';

	type BudgetNumberKey = 'max_llm_requests' | 'wall_clock_seconds' | 'max_total_tokens' | 'max_cost_usd';

	interface Props {
		value: AgentConfigOverrides;
		inherited: { values: Record<string, unknown>; sources: Record<string, string> };
		limits: Partial<Record<string, AgentLimit>>;
		networkPolicies: string[];
		idPrefix: string;
		errors?: Record<string, string>;
		invalid?: boolean;
		disabled?: boolean;
		onchange?: (value: AgentConfigOverrides) => void;
	}

	let {
		value = $bindable({}),
		inherited,
		limits,
		networkPolicies,
		idPrefix,
		errors = {},
		invalid = $bindable(false),
		disabled = false,
		onchange,
	}: Props = $props();

	const FIELDS: { key: BudgetNumberKey; label: string; scale: number; integer: boolean; step?: number }[] = [
		{ key: 'max_llm_requests', label: 'Max LLM requests', scale: 1, integer: true },
		{ key: 'wall_clock_seconds', label: 'Wall clock (minutes)', scale: 60, integer: true, step: 1 },
		{ key: 'max_total_tokens', label: 'Max total tokens', scale: 1, integer: true },
		{ key: 'max_cost_usd', label: 'Cost cap per run (USD)', scale: 1, integer: false, step: 0.5 },
	];

	const NETWORK_LABELS: Record<string, string> = {
		packages: 'Package registries only',
		none: 'No network',
	};

	const SOURCE_LABELS: Record<string, string> = {
		default: 'platform default',
		preset: 'preset',
		inline: 'override',
	};

	let fieldInvalid = $state<Record<string, boolean>>(Object.fromEntries(FIELDS.map((f) => [f.key, false])));

	$effect(() => {
		invalid = Object.values(fieldInvalid).some(Boolean);
	});

	function setKey(key: keyof AgentConfigOverrides, next: unknown) {
		const copy: Record<string, unknown> = { ...value };
		if (next === null || next === undefined || next === '') delete copy[key];
		else copy[key] = next;
		value = copy as AgentConfigOverrides;
		onchange?.(value);
	}

	function source(key: string): string {
		const s = inherited.sources[key];
		return s ? (SOURCE_LABELS[s] ?? s) : '';
	}

	function placeholder(key: BudgetNumberKey, scale: number): string {
		const v = inherited.values[key];
		if (typeof v !== 'number') return 'platform default';
		const shown = (v / scale).toLocaleString(undefined, { maximumFractionDigits: 3 });
		const from = source(key);
		return from ? `${shown} · ${from}` : shown;
	}

	const networkItems = $derived(
		networkPolicies.map((p) => ({ value: p, label: NETWORK_LABELS[p] ?? p }))
	);
	const inheritedNetwork = $derived.by(() => {
		const v = inherited.values.network_policy;
		if (typeof v !== 'string') return 'platform default';
		const from = source('network_policy');
		return `${NETWORK_LABELS[v] ?? v}${from ? ` · ${from}` : ''}`;
	});
</script>

<div class="grid gap-3 @md:grid-cols-2 @2xl:grid-cols-3">
	{#each FIELDS as field (field.key)}
		{@const limit = limits[field.key]}
		<NumberField
			id="{idPrefix}-{field.key}"
			label={field.label}
			value={(value[field.key] as number | undefined) ?? null}
			min={limit?.min}
			max={limit?.max}
			integer={field.integer && limit?.type !== 'float'}
			scale={field.scale}
			step={field.step}
			placeholder={placeholder(field.key, field.scale)}
			error={errors[field.key]}
			bind:invalid={fieldInvalid[field.key]}
			{disabled}
			onchange={(v) => setKey(field.key, v)}
		/>
	{/each}
	<div class="min-w-0 space-y-1.5">
		<Label class="text-xs" for="{idPrefix}-network_policy">Network</Label>
		<Select.Simple
			id="{idPrefix}-network_policy"
			size="sm"
			value={value.network_policy ?? ''}
			items={networkItems}
			placeholder={inheritedNetwork}
			clearLabel="Inherit ({inheritedNetwork})"
			class={value.network_policy ? 'border-primary/50' : ''}
			aria-describedby={errors.network_policy ? `${idPrefix}-network_policy-note` : undefined}
			{disabled}
			onValueChange={(v) => setKey('network_policy', v || undefined)}
		/>
		{#if errors.network_policy}
			<p id="{idPrefix}-network_policy-note" class="text-[11px] text-danger-fg">{errors.network_policy}</p>
		{/if}
	</div>
</div>
