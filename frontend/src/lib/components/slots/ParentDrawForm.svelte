<script lang="ts">
	/**
	 * ParentDrawForm — the knobs of a random parent draw (`ParentSelection`, spec 7.2): k, stratification by
	 * parent model, the seed (empty = derived; "randomise" picks one) and a minimum pass rate. The pool filters
	 * (task, stack, models, harnesses) come from the ParentPicker's toolbar.
	 *
	 * Props: `form` (bindable `ParentDrawForm`), `errors` (field → message), `poolSize`.
	 */
	import NumberInput from './NumberInput.svelte';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import Shuffle from '@lucide/svelte/icons/shuffle';
	import { randomSeed, type ParentDrawForm } from './selection';

	interface Props {
		form: ParentDrawForm;
		errors?: Record<string, string>;
		poolSize?: number | null;
	}

	let { form = $bindable(), errors = {}, poolSize = null }: Props = $props();

	function set<K extends keyof ParentDrawForm>(key: K, value: ParentDrawForm[K]) {
		form = { ...form, [key]: value };
	}
</script>

<div class="grid gap-3 sm:grid-cols-2">
	<div class="space-y-1">
		<Label for="draw-k">Parents to draw (k)</Label>
		<NumberInput id="draw-k" min={1} max={poolSize ?? undefined} step={1} value={form.k} onvaluechange={(v) => set('k', v)} invalid={!!errors.k} />
		{#if errors.k}<p class="text-[11px] text-danger-fg">{errors.k}</p>{:else if poolSize != null}<p class="text-[11px] text-muted-foreground">{poolSize} in the pool</p>{/if}
	</div>
	<div class="space-y-1">
		<Label>Stratify</Label>
		<Segmented
			size="sm"
			aria-label="Stratify the draw"
			value={form.stratify_by}
			onValueChange={(v) => set('stratify_by', v as ParentDrawForm['stratify_by'])}
			items={[
				{ value: 'parent_model', label: 'By parent model', title: 'Draw evenly across the models that wrote the parents' },
				{ value: 'none', label: 'None' },
			]}
		/>
	</div>
	<div class="space-y-1">
		<Label for="draw-seed">Seed</Label>
		<div class="flex gap-1.5">
			<NumberInput id="draw-seed" min={0} step={1} placeholder="derived when empty" value={form.seed} onvaluechange={(v) => set('seed', v)} invalid={!!errors.seed} />
			<Button variant="outline" size="icon" title="Randomise the seed" aria-label="Randomise the seed" onclick={() => set('seed', randomSeed())}>
				<Shuffle class="h-4 w-4" />
			</Button>
		</div>
		{#if errors.seed}<p class="text-[11px] text-danger-fg">{errors.seed}</p>{/if}
	</div>
	<div class="space-y-1">
		<Label for="draw-min">Minimum pass rate</Label>
		<NumberInput id="draw-min" min={0} max={1} step={0.05} placeholder="any" value={form.min_pass_rate} onvaluechange={(v) => set('min_pass_rate', v)} invalid={!!errors.min_pass_rate} />
		{#if errors.min_pass_rate}<p class="text-[11px] text-danger-fg">{errors.min_pass_rate}</p>{:else}<p class="text-[11px] text-muted-foreground">0–1, agent checks</p>{/if}
	</div>
	{#if errors.task}<p class="text-[11px] text-danger-fg sm:col-span-2">{errors.task}</p>{/if}
</div>
