<script lang="ts">
	/**
	 * AnalysisPlanEditor — the pre-registered analysis plan (frozen with the
	 * design at first open): primary metric, planned sample, rule-based
	 * exclusions and the policy for apps nobody could evaluate.
	 *
	 * Props: `id`, `plan` (bindable), `blocks`, `allocationStrategy` (shows only
	 * the planned-sample field that applies; both when unknown), `errors`
	 * (relative to the plan), `readOnly`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import type { AllocationStrategy, AnalysisPlan, Block } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, messagesUnder, under, type FieldError } from './errors';
	import { metricOptions, metricValue, parseMetricValue, plannedSampleField, primaryIsValid } from './plan';

	interface Props {
		id: string;
		plan: AnalysisPlan;
		blocks: Block[];
		allocationStrategy?: AllocationStrategy | null;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, plan = $bindable(), blocks, allocationStrategy = null, errors = [], readOnly = false }: Props = $props();

	const options = $derived(metricOptions(blocks));
	const primaryValue = $derived(plan.primary ? metricValue(plan.primary.block_id, plan.primary.metric) : '');
	const dangling = $derived(!primaryIsValid(plan, blocks));
	const sampleField = $derived(plannedSampleField(allocationStrategy));

	const RULES: { key: keyof AnalysisPlan['exclusion_rules']; label: string; help: string }[] = [
		{
			key: 'speeding',
			label: 'Speeding',
			help: 'Assignments whose active and wall-clock time are both below the minimum time per item (Settings › Quality).',
		},
		{
			key: 'attention_failed',
			label: 'Failed attention checks',
			help: 'All assignments of participants who answered an attention-check question differently from its expected option.',
		},
		{
			key: 'straightlining',
			label: 'Straight-lining',
			help: 'Assignments where a rating block with four or more criteria got the same value everywhere.',
		},
		{
			key: 'app_not_viewed',
			label: 'App never viewed',
			help: 'Submitted assignments in which an app was never shown running.',
		},
	];

	function setPrimary(value: string) {
		plan.primary = value ? parseMetricValue(value) : null;
	}
</script>

<div class="space-y-5">
	<div class="space-y-1.5">
		<Label class="text-xs" for="{id}-primary">Primary outcome</Label>
		{#if readOnly}
			<p class="text-sm">
				{options.find((o) => o.value === primaryValue)?.label ?? (plan.primary ? `${plan.primary.block_id} / ${plan.primary.metric}` : 'None — results are exploratory')}
			</p>
		{:else}
			<Select.Simple
				id="{id}-primary"
				class="sm:w-[28rem]"
				value={dangling ? '' : primaryValue}
				placeholder={options.length ? 'No primary outcome (exploratory)' : 'Add a block with a metric first'}
				clearLabel="No primary outcome (exploratory)"
				items={options.map((o) => ({ value: o.value, label: o.label, description: o.description }))}
				onValueChange={setPrimary}
				disabled={options.length === 0}
				aria-invalid={messagesUnder(errors, 'primary').length ? true : undefined}
			/>
		{/if}
		{#if dangling && plan.primary}
			<div class="flex flex-wrap items-center gap-2 text-[11px] text-warning-fg">
				<span>The primary outcome “{plan.primary.block_id} / {plan.primary.metric}” no longer exists in the design.</span>
				{#if !readOnly}<Button variant="outline" size="sm" class="h-6 text-[11px]" onclick={() => (plan.primary = null)}>Clear</Button>{/if}
			</div>
		{/if}
		<p class="text-[11px] {messagesUnder(errors, 'primary').length ? 'text-danger-fg' : 'text-muted-foreground'}">
			{messagesUnder(errors, 'primary').join(' ') ||
				'Results are confirmatory only when a primary outcome is set, the planned sample is reached and the task is closed once.'}
		</p>
	</div>

	<div class="grid gap-3 sm:grid-cols-2">
		{#if sampleField !== 'planned_participants'}
			<NumberField
				id="{id}-planned-rpi"
				label="Planned ratings per item{sampleField ? '' : ' (balanced allocation)'}"
				bind:value={plan.planned_ratings_per_item}
				min={1}
				max={50}
				placeholder="Not planned"
				disabled={readOnly}
				hint="Submitted ratings each app (or pair) should reach."
				error={messageAt(errors, 'planned_ratings_per_item')}
			/>
		{/if}
		{#if sampleField !== 'planned_ratings_per_item'}
			<NumberField
				id="{id}-planned-participants"
				label="Planned participants{sampleField ? '' : ' (every-item allocation)'}"
				bind:value={plan.planned_participants}
				min={1}
				max={10000}
				placeholder="Not planned"
				disabled={readOnly}
				hint="Participants who complete every item."
				error={messageAt(errors, 'planned_participants')}
			/>
		{/if}
	</div>

	<fieldset class="space-y-2">
		<legend class="section-label mb-1">Exclusion rules</legend>
		<p class="text-[11px] text-muted-foreground">
			Applied when results are computed and reported per rule; rows are never deleted or changed.
		</p>
		{#each RULES as rule (rule.key)}
			<label class="flex items-start gap-2.5 text-sm">
				<Checkbox class="mt-0.5" bind:checked={plan.exclusion_rules[rule.key]} disabled={readOnly} />
				<span>
					<span class="font-medium">{rule.label}</span>
					<span class="block text-xs text-muted-foreground">{rule.help}</span>
				</span>
			</label>
		{/each}
		{#each messagesUnder(under(errors, 'exclusion_rules')) as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
	</fieldset>

	<div class="space-y-1.5">
		<p class="section-label" id="{id}-unevaluable-label">Apps nobody could evaluate</p>
		<Segmented
			size="sm"
			aria-labelledby="{id}-unevaluable-label"
			value={plan.unevaluable_policy}
			disabled={readOnly}
			onValueChange={(v) => (plan.unevaluable_policy = v === 'floor' ? 'floor' : 'exclude')}
			items={[
				{ value: 'exclude', label: 'Exclude (complete case)' },
				{ value: 'floor', label: 'Count at the floor' },
			]}
		/>
		<p class="text-[11px] {messageAt(errors, 'unevaluable_policy') ? 'text-danger-fg' : 'text-muted-foreground'}">
			{messageAt(errors, 'unevaluable_policy') ||
				(plan.unevaluable_policy === 'floor'
					? 'An app with no submitted rating and at least one “app is broken” skip scores the scale minimum (compliance 0) in group means.'
					: 'Unevaluable apps are left out of group means; results always also report a floor sensitivity value.')}
		</p>
	</div>

	{#each messagesAt(errors) as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
