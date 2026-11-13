<script lang="ts">
	/**
	 * ReplicationStep — repeats (quick picks plus an exact field) beside the precision they buy, the
	 * seeds and run order (folded: they have good random defaults), and the Budget preset: the limits
	 * enforced outside the model by the gateway and the scheduler.
	 *
	 * Props: `builder`, `locked`, `units` (tasks or targets chosen), `warnOnFingerprintChange`.
	 */
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import NumberField from '../../shared/NumberField.svelte';
	import { MODE_LABELS } from '../../format';
	import type { BuilderState } from '../builder.svelte';
	import { REPEAT_LIMITS, clampRepeats, randomSeed } from '../design';
	import BuilderGroup from '../BuilderGroup.svelte';
	import PowerHelper from '../PowerHelper.svelte';
	import ResourcePolicyPicker from '../ResourcePolicyPicker.svelte';
	import Dices from '@lucide/svelte/icons/dices';

	interface Props {
		builder: BuilderState;
		locked: boolean;
		units: number;
		warnOnFingerprintChange: boolean;
	}

	let { builder: b, locked, units, warnOnFingerprintChange }: Props = $props();

	const design = $derived(b.design);
	const limits = $derived(REPEAT_LIMITS[design.mode]);
	const quick = $derived(design.mode === 'slot' ? [10, 30, 50, 100] : [1, 3, 5, 10]);

	function setRepeats(n: number) {
		b.setDesign({ ...design, repeats: clampRepeats(design.mode, n) });
	}
</script>

<BuilderGroup title="Repeats" description="Independent runs of every condition on every {design.mode === 'app' ? 'task' : 'target'}. Each repeat gets the same seed in every condition (common random numbers).">
	<div class="grid gap-4 @3xl:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
		<div class="space-y-3">
			<div class="flex flex-wrap gap-1.5" role="group" aria-label="Common repeat counts">
				{#each quick as n (n)}
					<button
						type="button"
						class={cn('rp-quick', design.repeats === n && 'rp-quick-on')}
						aria-pressed={design.repeats === n}
						disabled={locked}
						onclick={() => setRepeats(n)}
					>
						{n}
					</button>
				{/each}
			</div>
			<NumberField
				id="repeats"
				label="Repeats per condition × {design.mode === 'app' ? 'task' : 'target'}"
				value={design.repeats}
				min={limits.min}
				max={limits.max}
				required
				disabled={locked}
				hint="{limits.min}–{limits.max} in {MODE_LABELS[design.mode].toLowerCase()} mode. 3 or more lets the report show a spread."
				onchange={(v) => v !== null && setRepeats(v)}
			/>
		</div>
		<PowerHelper mode={design.mode} {units} repeats={design.repeats} />
	</div>
</BuilderGroup>

<BuilderGroup title="Seeds & run order" summary="Base seed {design.base_seed} · order seed {design.order_seed}" collapsible>
	<p class="text-xs text-muted-foreground">
		The base seed fixes each (unit, repeat) across conditions; the order seed shuffles the units and the conditions inside each block. Keep
		them to reproduce this design exactly.
	</p>
	<div class="grid max-w-xl gap-3 @lg:grid-cols-2">
		<NumberField id="base-seed" label="Base seed" value={design.base_seed} min={0} max={2 ** 31 - 1} required disabled={locked} onchange={(v) => v !== null && b.setDesign({ ...design, base_seed: v })} />
		<NumberField id="order-seed" label="Order seed" value={design.order_seed} min={0} max={2 ** 31 - 1} required disabled={locked} onchange={(v) => v !== null && b.setDesign({ ...design, order_seed: v })} />
	</div>
	<div>
		<Button variant="outline" size="sm" disabled={locked} onclick={() => b.setDesign({ ...design, base_seed: randomSeed(), order_seed: randomSeed() })}>
			<Dices />Draw new seeds
		</Button>
	</div>
</BuilderGroup>

<BuilderGroup
	title="Budget"
	description="Limits enforced outside the model: the gateway per run, the scheduler per cell and for the whole experiment. Budget values never enter a prompt."
>
	<ResourcePolicyPicker
		value={design.resource_policy}
		disabled={locked}
		{warnOnFingerprintChange}
		onchange={(ref) => b.setDesign({ ...design, resource_policy: ref })}
	/>
</BuilderGroup>

<style>
	.rp-quick {
		min-width: 2.75rem;
		height: 2rem;
		cursor: pointer;
		border: 1px solid var(--border);
		border-radius: calc(var(--radius) - 1px);
		padding-inline: 0.625rem;
		font-family: var(--font-mono);
		font-size: 0.8125rem;
		font-variant-numeric: tabular-nums;
		transition:
			border-color 150ms,
			background-color 150ms;
	}
	.rp-quick:hover:not(:disabled) {
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
	}
	.rp-quick-on {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 12%, transparent);
		color: var(--primary);
		font-weight: 600;
	}
	.rp-quick:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.rp-quick:disabled {
		cursor: not-allowed;
		opacity: 0.5;
	}
</style>
