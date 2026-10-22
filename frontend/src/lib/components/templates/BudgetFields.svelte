<script lang="ts">
	/**
	 * BudgetFields — sparse editor for agent-run limits and the network policy
	 * (a budget preset's config, or inline budget overrides).
	 *
	 * Props:
	 * - `values` (bindable `BudgetPresetConfig`): only explicitly set keys; a blank
	 *   field removes its key. Wall clock is edited in minutes, stored in seconds.
	 * - `config`: `GET /agent/config/` (limits + platform defaults); null → the
	 *   fields still work, the server validates the ranges.
	 * - `inherited`: `{ values, source }` of the layer below (e.g. a budget preset),
	 *   shown in placeholders as "30 · preset"; otherwise the platform default.
	 * - `idPrefix` (required), `disabled`, `invalid` (bindable output: a field holds
	 *   a value that was not applied), `onchange(values)` after each edit.
	 */
	import type { AgentConfigInfo, BudgetPresetConfig, NetworkPolicy } from '$lib/api/generation';
	import { cn } from '$lib/utils';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import { numberInputValue } from '$lib/components/generator/llm-params';
	import {
		BUDGET_FIELDS,
		NETWORK_POLICY_LABELS,
		budgetDefault,
		budgetProblem,
		budgetRange,
		budgetStoredValue,
		type BudgetField,
	} from './presets';

	interface Props {
		values: BudgetPresetConfig;
		config: AgentConfigInfo | null;
		inherited?: { values: BudgetPresetConfig; source: string } | null;
		idPrefix: string;
		disabled?: boolean;
		invalid?: boolean;
		onchange?: (values: BudgetPresetConfig) => void;
	}

	let {
		values = $bindable({}),
		config,
		inherited = null,
		idPrefix,
		disabled = false,
		invalid = $bindable(false),
		onchange,
	}: Props = $props();

	// An error stays visible only while the stored value is still the one it
	// was raised against (a parent reset hides it).
	let errors = $state<Record<string, { message: string; basis: unknown }>>({});

	function shown(field: BudgetField): number | undefined {
		const stored = values[field.key];
		return typeof stored === 'number' ? stored / field.scale : undefined;
	}

	function fieldError(field: BudgetField): string {
		const entry = errors[field.key];
		return entry && entry.basis === values[field.key] ? entry.message : '';
	}

	function setValue(key: keyof BudgetPresetConfig, value: number | string | undefined) {
		const next: Record<string, unknown> = { ...values };
		if (value === undefined || value === '') delete next[key];
		else next[key] = value;
		values = next as BudgetPresetConfig;
		onchange?.(values);
	}

	function setError(field: BudgetField, message: string) {
		if (!message) {
			if (field.key in errors) {
				const { [field.key]: _removed, ...rest } = errors;
				errors = rest;
			}
			return;
		}
		errors = { ...errors, [field.key]: { message, basis: values[field.key] } };
	}

	function onInput(field: BudgetField, event: Event) {
		const el = event.currentTarget as HTMLInputElement;
		const raw = el.value.trim();
		if (raw === '') {
			if (el.validity.badInput) return setError(field, 'Incomplete number');
			setError(field, '');
			setValue(field.key, undefined);
			return;
		}
		const value = Number(raw);
		const problem = budgetProblem(field, value, config);
		if (problem) return setError(field, problem);
		setError(field, '');
		setValue(field.key, budgetStoredValue(field, value));
	}

	function formatShown(field: BudgetField, stored: number): string {
		const value = stored / field.scale;
		const text = Number.isInteger(value) ? value.toLocaleString() : String(Number(value.toFixed(2)));
		return field.key === 'max_cost_usd' ? `$${text}` : text;
	}

	function placeholder(field: BudgetField): string {
		const below = inherited?.values?.[field.key];
		if (typeof below === 'number') return `${formatShown(field, below)} · ${inherited?.source}`;
		const platform = budgetDefault(field, config);
		return platform != null ? `default ${formatShown(field, platform * field.scale)}` : 'platform default';
	}

	const networkItems = $derived(
		(config?.network_policies?.length ? config.network_policies : Object.keys(NETWORK_POLICY_LABELS)).map(
			(value) => ({ value, label: NETWORK_POLICY_LABELS[value] ?? value })
		)
	);
	const networkInherited = $derived.by(() => {
		const below = inherited?.values?.network_policy;
		if (below) return `${NETWORK_POLICY_LABELS[below] ?? below} · ${inherited?.source}`;
		const platform = config?.defaults?.network_policy;
		return platform ? `Platform default (${NETWORK_POLICY_LABELS[platform] ?? platform})` : 'Platform default';
	});

	$effect(() => {
		invalid = BUDGET_FIELDS.some((f) => !!fieldError(f));
	});
</script>

<div class="grid gap-3 sm:grid-cols-2" data-slot="budget-fields">
	{#each BUDGET_FIELDS as field (field.key)}
		{@const id = `${idPrefix}-${field.key}`}
		{@const error = fieldError(field)}
		{@const range = budgetRange(field, config)}
		{@const value = shown(field)}
		<div class="min-w-0 space-y-1.5">
			<Label class="text-xs" for={id}>{field.label}</Label>
			<Input
				{id}
				type="number"
				inputmode={field.integer ? 'numeric' : 'decimal'}
				min={range.min}
				max={range.max}
				step={field.step}
				value={numberInputValue(value)}
				placeholder={placeholder(field)}
				aria-invalid={error ? true : undefined}
				aria-describedby={error ? `${id}-error` : undefined}
				class={cn('h-8 text-sm tabular-nums', value != null && 'border-primary/50')}
				{disabled}
				oninput={(e: Event) => onInput(field, e)}
			/>
			{#if error}
				<p id="{id}-error" class="text-[11px] text-danger-fg">{error}</p>
			{/if}
		</div>
	{/each}
	<div class="min-w-0 space-y-1.5 sm:col-span-2">
		<Label class="text-xs" for="{idPrefix}-network">Network during the run</Label>
		<Select.Simple
			id="{idPrefix}-network"
			size="sm"
			class={cn('sm:max-w-sm', values.network_policy && 'border-primary/50')}
			value={values.network_policy ?? ''}
			items={networkItems}
			placeholder={networkInherited}
			clearLabel="Inherit — {networkInherited}"
			{disabled}
			onValueChange={(v) => setValue('network_policy', (v || undefined) as NetworkPolicy | undefined)}
		/>
	</div>
</div>
