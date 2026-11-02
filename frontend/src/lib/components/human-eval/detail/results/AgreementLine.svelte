<script lang="ts">
	/**
	 * AgreementLine — inter-rater agreement in one line: Krippendorff's alpha at
	 * its measurement level, percent agreement over pairable rater pairs, and
	 * the units / values it rests on ("not computable" when alpha is null).
	 *
	 * Props: `agreement`, `unit` (what a unit is: "apps", "items", "findings").
	 */
	import type { Agreement } from '$lib/api/human-eval';
	import { cn } from '$lib/utils';
	import { formatCoef, formatShare, plural } from '../../format';

	let { agreement, unit = 'units' }: { agreement: Agreement; unit?: string } = $props();

	const low = $derived(agreement.alpha != null && agreement.n_units >= 5 && agreement.alpha < 0.667);
</script>

<p class="text-xs text-muted-foreground">
	<span class="font-medium text-foreground">Agreement</span> ·
	Krippendorff’s α ({agreement.level})
	{#if agreement.alpha != null}
		<span class={cn('font-mono', low ? 'text-warning-fg' : 'text-foreground')}>{formatCoef(agreement.alpha)}</span>{#if low}{' '}<span class="text-warning-fg">(below 0.667)</span>{/if}
	{:else}
		<span>not computable</span>
	{/if}
	· {agreement.percent_agreement != null ? `${formatShare(agreement.percent_agreement)} of rater pairs agree` : 'no pairable ratings'}
	· {plural(agreement.n_units, unit.replace(/s$/, ''), unit)} with ≥ 2 ratings, {plural(agreement.n_values, 'value')}
</p>
