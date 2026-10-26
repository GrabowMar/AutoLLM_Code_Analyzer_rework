<script lang="ts">
	/**
	 * OutcomesEditor — how a profile turns findings into outcomes.
	 *
	 * Props: `config` (`OutcomeConfig`, replaced never mutated),
	 * `onchange(outcomes)`, `errors` (keys relative to the outcomes:
	 * `severity_weights.high`, `min_sloc_for_density`, ...), `readonly`,
	 * `idPrefix` (default 'outcomes').
	 */
	import { SEVERITIES, type OutcomeConfig, type Severity } from '$lib/api/analysis';
	import { severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import NumberField from './NumberField.svelte';
	import { SEVERITY_LABELS } from './profile-utils';

	interface Props {
		config: OutcomeConfig;
		onchange?: (config: OutcomeConfig) => void;
		errors?: Record<string, string>;
		readonly?: boolean;
		idPrefix?: string;
	}

	let { config, onchange, errors = {}, readonly = false, idPrefix = 'outcomes' }: Props = $props();

	function set<K extends keyof OutcomeConfig>(key: K, value: OutcomeConfig[K]) {
		onchange?.({ ...config, [key]: value });
	}

	function setWeight(severity: Severity, value: number) {
		set('severity_weights', { ...config.severity_weights, [severity]: value });
	}

	const id = (name: string) => `${idPrefix}-${name}`;
</script>

<div class="space-y-6" data-slot="outcomes-editor">
	{#if errors['']}
		<p
			class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg"
			role="alert"
		>
			{errors['']}
		</p>
	{/if}

	<section class="space-y-3" aria-labelledby={id('counting')}>
		<h4 id={id('counting')} class="section-label">What counts as an issue</h4>
		<div class="flex items-start gap-3">
			<Switch
				id={id('count-style')}
				checked={config.count_style}
				label="Count style findings"
				describedBy={id('count-style-help')}
				disabled={readonly}
				onCheckedChange={(on) => set('count_style', on)}
			/>
			<div class="min-w-0">
				<Label for={id('count-style')}>Count style findings</Label>
				<p id={id('count-style-help')} class="mt-1 text-[11px] leading-4 text-muted-foreground">
					Spelling and style findings (codespell) are reported but not counted by default.
				</p>
				{#if errors['count_style']}<p class="text-[11px] text-danger-fg">
						{errors['count_style']}
					</p>{/if}
			</div>
		</div>
		<div class="flex items-start gap-3">
			<Switch
				id={id('count-metrics')}
				checked={config.count_metric_findings}
				label="Count metric findings"
				describedBy={id('count-metrics-help')}
				disabled={readonly}
				onCheckedChange={(on) => set('count_metric_findings', on)}
			/>
			<div class="min-w-0">
				<Label for={id('count-metrics')}>Count metric findings</Label>
				<p id={id('count-metrics-help')} class="mt-1 text-[11px] leading-4 text-muted-foreground">
					Complexity ranks and duplicated blocks stay metrics unless counted here.
				</p>
				{#if errors['count_metric_findings']}<p class="text-[11px] text-danger-fg">
						{errors['count_metric_findings']}
					</p>{/if}
			</div>
		</div>
	</section>

	<section class="max-w-xs space-y-1.5">
		<Label for={id('min-sloc')} class="text-sm">Minimum SLOC for densities</Label>
		<NumberField
			id={id('min-sloc')}
			value={config.min_sloc_for_density}
			integer
			min={0}
			max={1_000_000}
			unit="SLOC"
			error={errors['min_sloc_for_density']}
			disabled={readonly}
			describedBy={id('min-sloc-help')}
			onchange={(n) => n !== null && set('min_sloc_for_density', n)}
		/>
		<p id={id('min-sloc-help')} class="text-[11px] leading-4 text-muted-foreground">
			Below this measured SLOC every per-KLOC density is missing (shown as —), never 0: tiny samples
			give unstable rates.
		</p>
	</section>

	<section class="space-y-3" aria-labelledby={id('weights')}>
		<div>
			<h4 id={id('weights')} class="section-label">Severity weights</h4>
			<p class="mt-1 text-[11px] leading-4 text-muted-foreground">
				Only feed the secondary weighted score; densities and the primary outcomes count issues, not
				weights.
			</p>
		</div>
		<div class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
			{#each SEVERITIES as severity (severity)}
				{@const key = `severity_weights.${severity}`}
				<div class="space-y-1.5">
					<Label for={id(`w-${severity}`)} class="text-xs">
						<span
							class={cn(
								'inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium',
								severityColors[severity]
							)}
						>
							{SEVERITY_LABELS[severity]}
						</span>
					</Label>
					<NumberField
						id={id(`w-${severity}`)}
						value={config.severity_weights[severity]}
						min={0}
						step={0.5}
						error={errors[key]}
						disabled={readonly}
						onchange={(n) => n !== null && setWeight(severity, n)}
					/>
				</div>
			{/each}
		</div>
		{#if errors['severity_weights']}<p class="text-[11px] text-danger-fg">
				{errors['severity_weights']}
			</p>{/if}
	</section>
</div>
