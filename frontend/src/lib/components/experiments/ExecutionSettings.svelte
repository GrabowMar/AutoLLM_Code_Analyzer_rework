<script lang="ts">
	/**
	 * ExecutionSettings — how the experiment's runs are ordered and admitted:
	 * scheduling policy (with its validity / throughput trade-off, a tiny
	 * ordering diagram and the preview's estimated model swaps), priority,
	 * a concurrency cap, infra retries, Ollama preloading and a resident-model
	 * override.
	 *
	 * Props:
	 * - `value` (bindable `ExecutionDraft`): replaced, never mutated.
	 * - `swaps`: estimated model swaps per endpoint for the current policy (preview).
	 * - `effective`: effective policy per endpoint (preview; shown for Auto).
	 * - `hasOllama`: show the preload switch. `maxResidentLimit`: smallest K of the used endpoints.
	 * - `invalid` (bindable output).
	 */
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import type { SchedulingPolicy } from '$lib/api/types/experiments';
	import type { ExecutionDraft } from './composer';
	import { POLICY_DESCRIPTIONS, POLICY_LABELS, policyLabel, priorityBucket } from './format';

	interface Props {
		value: ExecutionDraft;
		swaps?: Record<string, number> | null;
		effective?: Record<string, string> | null;
		hasOllama?: boolean;
		maxResidentLimit?: number | null;
		invalid?: boolean;
	}

	let {
		value = $bindable(),
		swaps = null,
		effective = null,
		hasOllama = false,
		maxResidentLimit = null,
		invalid = $bindable(false),
	}: Props = $props();

	let concurrencyInvalid = $state(false);
	let retriesInvalid = $state(false);
	let residentInvalid = $state(false);

	$effect(() => {
		invalid = concurrencyInvalid || retriesInvalid || residentInvalid;
	});

	const POLICIES: SchedulingPolicy[] = ['auto', 'interleaved', 'blocked', 'grouped'];
	const policyItems = POLICIES.map((p) => ({ value: p, label: POLICY_LABELS[p], title: POLICY_DESCRIPTIONS[p] }));
	const PRIORITIES = [
		{ value: '-5', label: 'Low', description: 'Yields to other experiments' },
		{ value: '0', label: 'Normal' },
		{ value: '5', label: 'High', description: 'Staff only; others are capped at Normal' },
	];

	// Three models (colours), two repeat blocks, two apps each.
	const ORDERS: Record<SchedulingPolicy, number[][]> = {
		interleaved: [
			[0, 2, 1, 1, 0, 2],
			[2, 0, 1, 0, 2, 1],
		],
		blocked: [
			[0, 0, 1, 1, 2, 2],
			[2, 2, 0, 0, 1, 1],
		],
		grouped: [
			[0, 0, 0, 0, 1, 1],
			[1, 1, 2, 2, 2, 2],
		],
		auto: [
			[0, 0, 1, 1, 2, 2],
			[2, 2, 0, 0, 1, 1],
		],
	};

	function patch(next: Partial<ExecutionDraft>) {
		value = { ...value, ...next };
	}

	const swapEntries = $derived(Object.entries(swaps ?? {}));
	const effectiveEntries = $derived(Object.entries(effective ?? {}));
</script>

<div class="space-y-4">
	<div class="space-y-2">
		<Label class="text-xs" id="exec-policy-label">Run order</Label>
		<Segmented
			aria-labelledby="exec-policy-label"
			size="sm"
			value={value.policy}
			items={policyItems}
			onValueChange={(v) => patch({ policy: v as SchedulingPolicy })}
		/>
		<div class="flex flex-col gap-3 rounded-md border border-border bg-muted/30 p-3 sm:flex-row sm:items-start">
			<div class="shrink-0 space-y-1" role="img" aria-label="Example order: three models (colours) over two repeat blocks">
				{#each ORDERS[value.policy] as block, b (b)}
					<div class="flex gap-0.5">
						{#each block as model, i (i)}
							<span class="size-3 rounded-[2px]" style="background: var(--chart-{model + 1})"></span>
						{/each}
					</div>
				{/each}
			</div>
			<div class="min-w-0 space-y-1 text-[11px]">
				<p class="text-foreground">{POLICY_DESCRIPTIONS[value.policy]}</p>
				{#if value.policy === 'grouped'}
					<p class="text-warning-fg">Results compare models run at different times; prefer it only for quick, exploratory runs.</p>
				{/if}
				{#if value.policy === 'auto' && effectiveEntries.length > 0}
					<p class="text-muted-foreground">
						Resolves to {effectiveEntries.map(([slug, p]) => `${policyLabel(p)} on ${slug}`).join(', ')}.
					</p>
				{/if}
				{#if swapEntries.length > 0}
					<p class="text-muted-foreground">
						Estimated model swaps: {swapEntries.map(([slug, n]) => `${slug} ${n}`).join(' · ')}
					</p>
				{/if}
			</div>
		</div>
	</div>

	<div class="grid gap-3 @lg:grid-cols-2 @3xl:grid-cols-4">
		<div class="space-y-1.5">
			<Label class="text-xs" for="exec-priority">Priority</Label>
			<Select.Simple
				id="exec-priority"
				size="sm"
				value={String(priorityBucket(value.priority))}
				items={PRIORITIES}
				onValueChange={(v) => patch({ priority: Number(v) })}
			/>
		</div>
		<NumberField
			id="exec-max-concurrency"
			label="Max concurrent runs"
			value={value.maxConcurrency}
			min={1}
			max={64}
			placeholder="Unlimited"
			bind:invalid={concurrencyInvalid}
			hint="Blank = as many as capacity allows."
			onchange={(n) => patch({ maxConcurrency: n })}
		/>
		<NumberField
			id="exec-infra-retries"
			label="Infra retries"
			value={value.infraRetries}
			min={0}
			max={5}
			required
			bind:invalid={retriesInvalid}
			hint="Same seed; only for runs that never got a model reply."
			onchange={(n) => patch({ infraRetries: n ?? 0 })}
		/>
		<NumberField
			id="exec-max-resident"
			label="Max resident models"
			value={value.maxResidentModels}
			min={1}
			max={maxResidentLimit ?? undefined}
			placeholder={maxResidentLimit != null ? `endpoint: ${maxResidentLimit}` : 'endpoint default'}
			bind:invalid={residentInvalid}
			hint="Fewer models loaded at once per endpoint (never above its limit)."
			onchange={(n) => patch({ maxResidentModels: n })}
		/>
	</div>

	{#if hasOllama}
		<div class="flex items-start gap-3">
			<Switch
				id="exec-warmup"
				checked={value.warmup}
				describedBy="exec-warmup-note"
				onCheckedChange={(v) => patch({ warmup: v })}
			/>
			<div class="min-w-0 space-y-0.5">
				<label for="exec-warmup" class="text-sm font-medium">Preload Ollama models before their runs</label>
				<p id="exec-warmup-note" class="text-[11px] text-muted-foreground">
					Loads a model before its first run is admitted, so loading time never counts against an agent's wall clock.
				</p>
			</div>
		</div>
	{/if}
</div>
