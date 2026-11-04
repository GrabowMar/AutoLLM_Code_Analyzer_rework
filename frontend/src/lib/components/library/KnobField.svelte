<script lang="ts">
	/**
	 * KnobField — one Budget preset knob: label, class badge ("affects treatment" for C/R, with the class
	 * explanation as tooltip), enforcement points, the input (by `inputKind`), the base value and a reset
	 * when the knob is an override. Used by `ResourcePolicyEditor`; emits the knob's new value.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import type { KnobMeta, StopRule } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import NumberInput from './NumberInput.svelte';
	import ListInput from './ListInput.svelte';
	import StopRulesEditor from './StopRulesEditor.svelte';
	import {
		classBadge,
		enumOptions,
		formatKnobValue,
		inputKind,
		isNullable,
		knobLabel,
		numberBounds,
		type BudgetBasis,
		type KnobState,
	} from './policy';

	interface Props {
		meta: KnobMeta;
		value: unknown;
		baseValue?: unknown;
		status: KnobState;
		budgetBasis?: BudgetBasis;
		readonly?: boolean;
		/** Lint hint for this knob (client mirror of the policy lint). */
		hint?: { severity: 'error' | 'warn'; message: string } | null;
		onchange: (value: unknown) => void;
		onreset?: () => void;
	}

	let { meta, value, baseValue, status, budgetBasis = 'usd', readonly = false, hint = null, onchange, onreset }: Props = $props();

	const kind = $derived(inputKind(meta));
	const badge = $derived(classBadge(meta, budgetBasis));
	const id = $derived(`knob-${meta.key.replace(/[^a-z0-9]+/gi, '-')}`);
	const bounds = $derived(numberBounds(meta));
	const nullable = $derived(isNullable(meta));
	const label = $derived(knobLabel(meta.key));

	const MIB = 1024 * 1024;
	const unitSuffix: Record<string, string> = {
		usd: 'USD',
		tokens: 'tokens',
		s: 's',
		h: 'h',
		requests: 'requests',
		retries: 'retries',
		turns: 'turns',
		runs: 'runs',
		repeats: 'repeats',
		models: 'models',
		evaluations: 'evaluations',
		usd_per_mtok: '$/Mtok',
		fraction: '0–1',
		ratio: '×',
		requests_per_min: 'req/min',
		tokens_per_s: 'tok/s',
	};

	// sandbox.memory: "2g" — validated before it is emitted.
	let memoryText = $state('');
	let memoryInvalid = $state(false);
	$effect.pre(() => {
		if (kind === 'text') memoryText = String(value ?? '');
	});
	function emitMemory() {
		const ok = /^[1-9][0-9]*[kmg]$/.test(memoryText.trim());
		memoryInvalid = !ok;
		if (ok) onchange(memoryText.trim());
	}

	// reasoning
	const reasoning = $derived((value ?? {}) as { mode: string; effort: string | null; max_tokens: number | null });
	function setReasoningMode(mode: string) {
		if (mode === 'effort') onchange({ mode, effort: reasoning.effort ?? 'low', max_tokens: null });
		else if (mode === 'max_tokens') onchange({ mode, effort: null, max_tokens: reasoning.max_tokens ?? 4096 });
		else onchange({ mode: 'provider_default', effort: null, max_tokens: null });
	}

	// routing.max_price
	const priceMode = $derived(value === null || value === undefined ? 'none' : typeof value === 'string' ? 'multiplier' : 'explicit');
	const multiplier = $derived(typeof value === 'string' ? Number(/([0-9.]+)$/.exec(value)?.[1] ?? 1) : 1.25);
	const price = $derived((typeof value === 'object' && value ? value : { prompt: 0, completion: 0 }) as { prompt: number; completion: number });
	function setPriceMode(mode: string) {
		if (mode === 'none') onchange(null);
		else if (mode === 'multiplier') onchange(`model×${multiplier || 1.25}`);
		else onchange({ prompt: price.prompt ?? 0, completion: price.completion ?? 0 });
	}

	const effortItems = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'].map((v) => ({ value: v, label: v }));
</script>

<div class={cn('knob-row grid gap-x-4 gap-y-1.5 py-2.5 sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]', status === 'override' && 'knob-row-override')}>
	<div class="min-w-0">
		<div class="flex flex-wrap items-center gap-1.5">
			<label for={id} class="text-sm font-medium">{label}</label>
			{#if badge.tone !== 'operational'}
				<span
					class={cn(
						'inline-flex cursor-help items-center rounded-sm border px-1 text-[11px] leading-4',
						badge.tone === 'treatment' ? 'border-primary/40 text-primary' : 'border-accent-2/50 text-foreground',
					)}
					title={badge.help}
					aria-label="{badge.label}: {badge.help}"
				>
					{badge.cls} · {badge.label}
				</span>
			{:else}
				<span class="inline-flex cursor-help items-center rounded-sm border border-border px-1 text-[11px] leading-4 text-muted-foreground" title={badge.help}>
					{badge.cls}
				</span>
			{/if}
			{#if status === 'override'}
				<span class="rounded-sm bg-primary/10 px-1 text-[11px] leading-4 text-primary">override</span>
			{/if}
		</div>
		<p class="mt-0.5 font-mono text-[11px] text-muted-foreground">{meta.key} · {meta.where.join(', ')}</p>
		<p class="mt-0.5 text-xs text-muted-foreground">{meta.description}</p>
	</div>

	<div class="min-w-0 space-y-1">
		{#if kind === 'number' || kind === 'bytes' || kind === 'nano_cpus'}
			{@const scale = kind === 'bytes' ? MIB : kind === 'nano_cpus' ? 1e9 : 1}
			<div class="flex items-center gap-2">
				<NumberInput
					{id}
					value={typeof value === 'number' ? value / scale : null}
					min={kind === 'number' ? bounds.min : 0}
					max={kind === 'number' ? bounds.max : undefined}
					step={kind === 'number' ? bounds.step : kind === 'nano_cpus' ? 0.5 : 1}
					placeholder={nullable ? 'unset' : undefined}
					{readonly}
					class="w-40"
					onchange={(n) => {
						if (n === null && !nullable) return;
						onchange(n === null ? null : kind === 'number' ? n : Math.round(n * scale));
					}}
				/>
				<span class="text-xs text-muted-foreground">{kind === 'bytes' ? 'MB' : kind === 'nano_cpus' ? 'CPU' : (unitSuffix[meta.unit ?? ''] ?? meta.unit ?? '')}</span>
				{#if nullable && value !== null && !readonly}
					<button type="button" class="cursor-pointer text-xs text-muted-foreground hover:text-foreground" onclick={() => onchange(null)}>Unset</button>
				{/if}
			</div>
		{:else if kind === 'bool'}
			<label class="flex items-center gap-2 text-sm">
				<Checkbox {id} checked={value === true} disabled={readonly} onCheckedChange={(c) => onchange(c === true)} />
				{value ? 'On' : 'Off'}
			</label>
		{:else if kind === 'enum'}
			<Select.Simple
				{id}
				value={String(value ?? '')}
				items={enumOptions(meta).map((o) => ({ value: o, label: o.replace(/_/g, ' ') }))}
				size="sm"
				class="w-56"
				disabled={readonly}
				onValueChange={(v) => onchange(v)}
			/>
		{:else if kind === 'text'}
			<Input
				{id}
				bind:value={memoryText}
				oninput={emitMemory}
				readonly={readonly}
				class={cn('h-8 w-40 font-mono', memoryInvalid && 'border-destructive')}
				aria-invalid={memoryInvalid || undefined}
			/>
			{#if memoryInvalid}<p class="text-xs text-danger-fg">Use a number with k, m or g (e.g. 2g).</p>{/if}
		{:else if kind === 'list'}
			<ListInput {id} value={(value as string[]) ?? []} {readonly} placeholder="none" class="w-full max-w-md" onchange={(v) => onchange(v)} />
		{:else if kind === 'fractions'}
			<ListInput
				{id}
				value={((value as number[]) ?? []).map(String)}
				{readonly}
				placeholder="0.5, 0.8"
				class="w-56"
				onchange={(v) => {
					const nums = v.map(Number);
					if (nums.every((n) => Number.isFinite(n) && n >= 0 && n <= 1)) onchange(nums);
				}}
			/>
		{:else if kind === 'reasoning'}
			<Segmented
				aria-label="Reasoning mode"
				value={reasoning.mode}
				size="sm"
				disabled={readonly}
				items={[
					{ value: 'provider_default', label: 'Provider default' },
					{ value: 'effort', label: 'Effort' },
					{ value: 'max_tokens', label: 'Token budget' },
				]}
				onValueChange={setReasoningMode}
			/>
			{#if reasoning.mode === 'effort'}
				<Select.Simple
					{id}
					value={reasoning.effort ?? ''}
					items={effortItems}
					size="sm"
					class="w-40"
					disabled={readonly}
					aria-label="Reasoning effort"
					onValueChange={(v) => onchange({ ...reasoning, effort: v || null, max_tokens: null })}
				/>
			{:else if reasoning.mode === 'max_tokens'}
				<div class="flex items-center gap-2">
					<NumberInput {id} value={reasoning.max_tokens} min={1} step={1} {readonly} class="w-40" aria-label="Reasoning token budget" onchange={(n) => onchange({ ...reasoning, effort: null, max_tokens: n })} />
					<span class="text-xs text-muted-foreground">tokens</span>
				</div>
			{/if}
		{:else if kind === 'max_price'}
			<Segmented
				aria-label="Max price"
				value={priceMode}
				size="sm"
				disabled={readonly}
				items={[
					{ value: 'none', label: 'No filter' },
					{ value: 'multiplier', label: '× model price' },
					{ value: 'explicit', label: 'Explicit $/Mtok' },
				]}
				onValueChange={setPriceMode}
			/>
			{#if priceMode === 'multiplier'}
				<div class="flex items-center gap-2">
					<span class="text-xs text-muted-foreground">model ×</span>
					<NumberInput {id} value={multiplier} min={0.01} step={0.05} {readonly} class="w-28" onchange={(n) => n && n > 0 && onchange(`model×${n}`)} />
				</div>
			{:else if priceMode === 'explicit'}
				<div class="flex flex-wrap items-center gap-2">
					<NumberInput {id} value={price.prompt} min={0} step={0.01} {readonly} class="w-28" aria-label="Prompt $/Mtok" onchange={(n) => n !== null && onchange({ ...price, prompt: n })} />
					<span class="text-xs text-muted-foreground">prompt</span>
					<NumberInput value={price.completion} min={0} step={0.01} {readonly} class="w-28" aria-label="Completion $/Mtok" onchange={(n) => n !== null && onchange({ ...price, completion: n })} />
					<span class="text-xs text-muted-foreground">completion · $/Mtok</span>
				</div>
			{/if}
		{:else if kind === 'study_cap' || kind === 'units'}
			{@const auto = kind === 'study_cap' ? value === 'auto' : value === 'all'}
			{@const token = kind === 'study_cap' ? 'auto' : 'all'}
			<div class="flex flex-wrap items-center gap-2">
				<Segmented
					aria-label={label}
					value={auto ? token : 'fixed'}
					size="sm"
					disabled={readonly}
					items={kind === 'study_cap'
						? [
								{ value: 'auto', label: 'Auto', title: 'p90 estimate × 1.1 / 1.25 / 1.5, confirmed at launch' },
								{ value: 'fixed', label: 'Fixed' },
							]
						: [
								{ value: 'all', label: 'All units' },
								{ value: 'fixed', label: 'Random k' },
							]}
					onValueChange={(v) => onchange(v === token ? token : kind === 'study_cap' ? 10 : 3)}
				/>
				{#if !auto}
					<NumberInput
						{id}
						value={typeof value === 'number' ? value : null}
						min={kind === 'study_cap' ? 0 : 1}
						step={kind === 'study_cap' ? 0.5 : 1}
						{readonly}
						class="w-32"
						onchange={(n) => n !== null && onchange(kind === 'units' ? Math.max(1, Math.round(n)) : n)}
					/>
					<span class="text-xs text-muted-foreground">{kind === 'study_cap' ? 'USD' : 'units'}</span>
				{/if}
			</div>
		{:else if meta.key === 'study.stop_rules'}
			<StopRulesEditor value={(value as StopRule[]) ?? []} {readonly} onchange={(v) => onchange(v)} />
		{:else}
			<pre class="max-h-40 overflow-auto rounded-md border border-border bg-muted/30 p-2 font-mono text-xs">{JSON.stringify(value, null, 2)}</pre>
		{/if}

		{#if hint}
			<p class={cn('text-xs', hint.severity === 'error' ? 'text-danger-fg' : 'text-warning-fg')}>{hint.message}</p>
		{/if}
		{#if status === 'override' && baseValue !== undefined}
			<p class="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
				<span>Base: <span class="font-mono">{formatKnobValue(meta, baseValue)}</span></span>
				{#if onreset && !readonly}
					<button type="button" class="inline-flex cursor-pointer items-center gap-1 text-primary hover:underline" onclick={onreset}>
						<RotateCcw class="size-3" aria-hidden="true" />Reset
					</button>
				{/if}
			</p>
		{/if}
	</div>
</div>
