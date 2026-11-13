<script lang="ts">
	/**
	 * ResourcePolicyEditor — the "Budget preset" editor (spec 4.2, 10.2 step 5). Shared with WP-15's study
	 * builder; it edits content only (saving, "Save as preset" and tier choice are the caller's).
	 *
	 *   <ResourcePolicyEditor value={content} base={tierPreset} knobMeta={knobs.knobs}
	 *                         warnOnFingerprintChange onchange={(v) => (content = v)} />
	 *
	 * Props:
	 * - `value`: the policy content (always complete); `onchange(next)` gets a new object per edit.
	 * - `base`: what overrides are measured against (the tier preset, or the version this one derives
	 *   from). Knobs equal to `base` are "inherited"; others are overrides with a Reset.
	 * - `knobMeta`: `GET /budget/knobs/`.`knobs` (class, where, ui_group, key_limit, type, unit, defaults).
	 * - `warnOnFingerprintChange`: warn when a class C/R knob differs from `base` (cloned or running study).
	 * - `budgetBasis`: the study's resolved basis (`run.max_cost_usd` is class O under `tokens`); defaults to
	 *   the policy's own `study.budget_basis`.
	 * - `readonly`; `keyLimitsOnly` hides the accordion groups (a compact summary view).
	 *
	 * Layout: the Key limits are always visible; the other knobs sit in collapsible groups (open when they
	 * hold an override). Every C/R knob carries an "affects treatment" badge whose tooltip explains the class.
	 */
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import { Button } from '$lib/components/ui/button';
	import type { KnobMeta, ResourcePolicyContent } from '$lib/api/v3/types';
	import KnobField from './KnobField.svelte';
	import {
		KEY_LIMITS_GROUP,
		POLICY_GROUPS,
		affectsTreatment,
		fingerprintChanges,
		getKnob,
		groupKnobs,
		knobClass,
		knobLabel,
		knobState,
		overrides,
		policyHints,
		resetKnob,
		setKnob,
		type BudgetBasis,
	} from './policy';

	interface Props {
		value: ResourcePolicyContent;
		base?: ResourcePolicyContent | null;
		knobMeta: KnobMeta[];
		warnOnFingerprintChange?: boolean;
		onchange: (v: ResourcePolicyContent) => void;
		readonly?: boolean;
		budgetBasis?: BudgetBasis;
		keyLimitsOnly?: boolean;
	}

	let {
		value,
		base = null,
		knobMeta,
		warnOnFingerprintChange = false,
		onchange,
		readonly = false,
		budgetBasis,
		keyLimitsOnly = false,
	}: Props = $props();

	const basis = $derived<BudgetBasis>(budgetBasis ?? value.study?.budget_basis ?? 'usd');
	const grouped = $derived(groupKnobs(knobMeta));
	const overridden = $derived(overrides(value, base, knobMeta));
	const overriddenSet = $derived(new Set(overridden));
	const fingerprint = $derived(warnOnFingerprintChange ? fingerprintChanges(value, base, knobMeta, basis) : []);
	const hints = $derived(new Map(policyHints(value).map((h) => [h.key, h])));
	const metaByKey = $derived(new Map(knobMeta.map((m) => [m.key, m])));

	/** Groups the user opened or closed (otherwise: open when they hold an override or a hint). */
	let toggled = $state<Record<string, boolean>>({});
	function isOpen(id: string, knobs: KnobMeta[]): boolean {
		if (id in toggled) return toggled[id];
		return knobs.some((k) => overriddenSet.has(k.key) || hints.has(k.key));
	}

	function set(key: string, v: unknown) {
		onchange(setKnob(value, key, v));
	}

	function resetAll() {
		if (base) onchange(structuredClone($state.snapshot(base)) as ResourcePolicyContent);
	}

	const treatmentCount = (knobs: KnobMeta[]) => knobs.filter((k) => affectsTreatment(knobClass(k, basis))).length;
</script>

{#snippet knobList(knobs: KnobMeta[])}
	<div class="divide-y divide-border/70">
		{#each knobs as meta (meta.key)}
			<KnobField
				{meta}
				value={getKnob(value, meta.key)}
				baseValue={base ? getKnob(base, meta.key) : undefined}
				status={knobState(value, base, meta.key)}
				budgetBasis={basis}
				{readonly}
				hint={hints.get(meta.key) ?? null}
				onchange={(v) => set(meta.key, v)}
				onreset={base ? () => onchange(resetKnob(value, base!, meta.key)) : undefined}
			/>
		{/each}
	</div>
{/snippet}

<div class="space-y-3">
	{#if fingerprint.length}
		<div class="flex gap-2 rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-sm" role="status">
			<TriangleAlert class="mt-0.5 size-4 shrink-0 text-warning-fg" aria-hidden="true" />
			<div class="min-w-0">
				<p class="font-medium">New fingerprint — not comparable with earlier runs</p>
				<p class="text-xs text-muted-foreground">
					{fingerprint.length} knob{fingerprint.length === 1 ? '' : 's'} that affect the treatment changed:
					{fingerprint.map((k) => knobLabel(k)).join(', ')}. Runs with the new values get a different config fingerprint.
				</p>
			</div>
		</div>
	{/if}

	<div class="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
		<span>
			{#if base}
				{overridden.length === 0 ? 'No overrides: every knob is inherited from the base.' : `${overridden.length} override${overridden.length === 1 ? '' : 's'} of the base.`}
			{:else}
				{knobMeta.length} knobs.
			{/if}
			Knobs marked <span class="text-primary">affects treatment</span> (class C/R) enter the generation policy hash.
		</span>
		{#if base && overridden.length && !readonly}
			<Button variant="ghost" size="sm" onclick={resetAll}><RotateCcw />Reset all</Button>
		{/if}
	</div>

	<section class="rounded-md border border-border bg-card" aria-labelledby="policy-key-limits">
		<header class="flex items-baseline justify-between gap-2 border-b border-border px-3 py-2">
			<h3 id="policy-key-limits" class="section-label">{KEY_LIMITS_GROUP.title}</h3>
			<span class="text-[11px] text-muted-foreground">{KEY_LIMITS_GROUP.description}</span>
		</header>
		<div class="px-3">
			{@render knobList(grouped.get(KEY_LIMITS_GROUP.id) ?? [])}
		</div>
		<p class="border-t border-border px-3 py-2 text-[11px] text-muted-foreground">
			Repeats are set per experiment (builder step 4), not in the preset.
		</p>
	</section>

	{#if !keyLimitsOnly}
		<div class="divide-y divide-border rounded-md border border-border bg-card">
			{#each POLICY_GROUPS as group (group.id)}
				{@const knobs = grouped.get(group.id) ?? []}
				{#if knobs.length}
					{@const open = isOpen(group.id, knobs)}
					{@const changed = knobs.filter((k) => overriddenSet.has(k.key)).length}
					<section aria-labelledby="policy-group-{group.id}">
						<h3 id="policy-group-{group.id}">
							<button
								type="button"
								class="flex w-full cursor-pointer items-center gap-2 px-3 py-2.5 text-left transition-colors duration-150 hover:bg-muted/50"
								aria-expanded={open}
								aria-controls="policy-group-body-{group.id}"
								onclick={() => (toggled = { ...toggled, [group.id]: !open })}
							>
								<ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform duration-150 {open ? 'rotate-90' : ''}" aria-hidden="true" />
								<span class="text-sm font-medium">{group.title}</span>
								<span class="font-mono text-[11px] text-muted-foreground">{knobs.length}</span>
								{#if treatmentCount(knobs)}
									<span class="text-[11px] text-muted-foreground">· {treatmentCount(knobs)} affect treatment</span>
								{/if}
								<span class="ml-auto flex items-center gap-1.5">
									{#if knobs.some((k) => hints.get(k.key)?.severity === 'error')}
										<span class="rounded-sm bg-destructive/15 px-1 text-[11px] text-danger-fg">check</span>
									{/if}
									{#if changed}
										<span class="rounded-sm bg-primary/10 px-1 text-[11px] text-primary">{changed} override{changed === 1 ? '' : 's'}</span>
									{/if}
								</span>
							</button>
						</h3>
						{#if open}
							<div id="policy-group-body-{group.id}" class="border-t border-border/70 px-3">
								<p class="pt-2 text-xs text-muted-foreground">{group.description}</p>
								{@render knobList(knobs)}
							</div>
						{/if}
					</section>
				{/if}
			{/each}
		</div>
	{/if}
	{#if metaByKey.size === 0}
		<p class="text-sm text-muted-foreground">The knob catalogue is not loaded.</p>
	{/if}
</div>
