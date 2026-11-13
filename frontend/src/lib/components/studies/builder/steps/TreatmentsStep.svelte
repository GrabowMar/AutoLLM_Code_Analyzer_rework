<script lang="ts">
	/**
	 * TreatmentsStep — every factor except the model (the Models step owns it): the stack (required in
	 * app mode, opens by itself while empty), harness, brief & prompt factors and run settings, in the
	 * FactorTable's groups. Above it, the condition count as arithmetic over the varied factors, so the
	 * cost of adding a level is visible before the plan answers. Grid options (fraction generator,
	 * dropping incompatible combinations, scheduling) are folded under "Grid & scheduling".
	 *
	 * Props: `builder`, `locked`, `onprompt(level)` (open the prompt override dialog).
	 */
	import type { Design, FactorSpec } from '$lib/api/v3/types';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Select from '$lib/components/ui/select';
	import type { BuilderState } from '../builder.svelte';
	import { crossedCounts, withFactors } from '../design';
	import BuilderGroup from '../BuilderGroup.svelte';
	import FactorTable from '../FactorTable.svelte';
	import { plural } from '../../format';

	interface Props {
		builder: BuilderState;
		locked: boolean;
		onprompt: (level: string) => void;
	}

	let { builder: b, locked, onprompt }: Props = $props();

	const design = $derived(b.design);
	const crossed = $derived(crossedCounts(design));
	const conditions = $derived(crossed.reduce((n, f) => n * f.count, 1));
	const fresh = $derived(b.planFresh && b.plan ? b.plan.n_conditions : null);
	const SCHEDULING: Record<Design['scheduling'], string> = {
		auto: 'automatic scheduling',
		interleaved: 'interleaved',
		blocked: 'blocked by model',
	};
	const gridSummary = $derived(
		[
			design.grid.type === 'fractional' ? `fractional (${design.grid.fraction?.generator || 'no generator yet'})` : 'full grid',
			b.dropIncompatible ? 'incompatible combinations dropped' : 'every combination kept',
			SCHEDULING[design.scheduling],
		].join(' · '),
	);
</script>

<div class="tr-formula" aria-live="polite">
	<span class="tr-formula-label">Conditions</span>
	{#if crossed.length === 0}
		<span class="text-xs text-muted-foreground">Nothing varies yet: one condition per model. Give a factor a second level to compare it.</span>
	{:else}
		<span class="tr-terms">
			{#each crossed as f, i (f.key)}
				{#if i > 0}<span class="tr-op" aria-hidden="true">×</span>{/if}
				<span class="tr-term"><b>{f.count}</b> {f.label.toLowerCase()}{f.count === 1 ? '' : 's'}</span>
			{/each}
			<span class="tr-op" aria-hidden="true">=</span>
			<span class="tr-total"><b>{fresh ?? conditions}</b> {(fresh ?? conditions) === 1 ? 'condition' : 'conditions'}</span>
		</span>
	{/if}
</div>

<FactorTable
	mode={design.mode}
	factors={design.factors}
	omit={['model']}
	disabled={locked}
	costByLevel={b.planFresh ? (b.plan?.estimate?.by_level ?? null) : null}
	oneditprompt={onprompt}
	onchange={(factors: FactorSpec[]) => b.setDesign(withFactors(design, factors))}
/>

{#if design.grid.type === 'fractional'}
	<div class="max-w-sm space-y-1.5">
		<Label for="grid-generator">Fraction generator</Label>
		<Input
			id="grid-generator"
			class="font-mono"
			placeholder="e.g. D=ABC"
			value={design.grid.fraction?.generator ?? ''}
			disabled={locked}
			aria-describedby="grid-generator-help"
			onchange={(e) =>
				b.setDesign({
					...design,
					grid: { ...design.grid, fraction: { generator: (e.currentTarget as HTMLInputElement).value.trim().toUpperCase() } },
				})}
		/>
		<p id="grid-generator-help" class="text-[11px] text-muted-foreground">
			A factor set to “Fraction” is generated from the crossed two-level factors; letters name them in table order (A = first).
		</p>
	</div>
{/if}

<BuilderGroup title="Grid & scheduling" summary={gridSummary} collapsible open={b.dropIncompatible || design.scheduling !== 'auto'}>
	<label class="flex items-start gap-2 text-xs">
		<Checkbox class="mt-px" checked={b.dropIncompatible} disabled={locked} onCheckedChange={(v) => b.setDropIncompatible(v === true)} />
		<span>
			<span class="font-medium text-foreground">Drop incompatible combinations</span>
			<span class="block text-muted-foreground">
				E.g. a style that doesn’t fit a stack. They are listed in the plan, and by-factor comparisons then use marginal means.
			</span>
		</span>
	</label>
	<div class="max-w-xs space-y-1.5">
		<Label for="scheduling" class="text-xs">Run order</Label>
		<Select.Simple
			id="scheduling"
			size="sm"
			value={design.scheduling}
			disabled={locked}
			items={[
				{ value: 'auto', label: 'Automatic', description: 'Blocked by model when a condition uses a local endpoint, else interleaved' },
				{ value: 'interleaved', label: 'Interleaved', description: 'Conditions alternate inside each block' },
				{ value: 'blocked', label: 'Blocked by model', description: 'Fewer model swaps on local endpoints' },
			]}
			onValueChange={(v) => b.setDesign({ ...design, scheduling: v as Design['scheduling'] })}
		/>
	</div>
	{#if fresh !== null}
		<p class="text-[11px] text-muted-foreground">The resolved plan has {plural(fresh, 'condition')}.</p>
	{/if}
</BuilderGroup>

<style>
	.tr-formula {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.75rem;
		padding: 0.625rem 0.875rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface-2);
	}
	.tr-formula-label {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		letter-spacing: 0.12em;
		text-transform: uppercase;
		color: var(--muted-foreground);
	}
	.tr-terms {
		display: flex;
		flex-wrap: wrap;
		align-items: baseline;
		gap: 0.375rem;
		font-size: 0.8125rem;
	}
	.tr-term b,
	.tr-total b {
		font-family: var(--font-mono);
		font-weight: 600;
		font-variant-numeric: tabular-nums;
	}
	.tr-op {
		font-family: var(--font-mono);
		color: var(--muted-foreground);
	}
	.tr-total {
		border-radius: calc(var(--radius) - 2px);
		background: color-mix(in oklab, var(--primary) 12%, transparent);
		padding: 0.0625rem 0.5rem;
		color: var(--primary);
	}
</style>
