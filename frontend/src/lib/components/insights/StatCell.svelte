<script lang="ts">
	/**
	 * One summary statistic (`{mean, median, stdev, n, n_missing}` or a binary
	 * `{share_true, n, n_missing}`): the mean, then "± sd · n · missing".
	 * Undefined values render "—" with the reason (mean at n=0, sd below n=2).
	 */
	import { isShareValue, type ReportOutcomeValue } from '$lib/api/reports';
	import NaValue from '$lib/components/analysis/NaValue.svelte';
	import { fmtNum, fmtShare } from './format';

	interface Props {
		stat: ReportOutcomeValue | null | undefined;
		digits?: number;
		/** Render the mean as a percentage of 0..1. */
		share?: boolean;
		/** Show the "± sd · n" line (default true). */
		detail?: boolean;
		/** Reason when there is no stat object at all. */
		missingReason?: string;
	}

	let {
		stat,
		digits = 2,
		share = false,
		detail = true,
		missingReason = 'Not reported for this group'
	}: Props = $props();

	function fmt(v: number | null | undefined, isShare: boolean): string | null {
		return isShare ? fmtShare(v, 1) : fmtNum(v, digits);
	}
</script>

{#if !stat}
	<NaValue reason={missingReason} />
{:else if isShareValue(stat)}
	{#if stat.share_true == null}
		<NaValue reason="No observation (n=0){stat.n_missing ? `; ${stat.n_missing} missing` : ''}" />
	{:else}
		<span class="font-mono tabular-nums">{fmtShare(stat.share_true, 0)}</span>
	{/if}
	{#if detail}
		<span class="block text-[11px] whitespace-nowrap text-muted-foreground">
			n={stat.n}{stat.n_missing ? ` · ${stat.n_missing} missing` : ''}
		</span>
	{/if}
{:else}
	{#if stat.mean == null}
		<NaValue
			reason="No observation (n=0){stat.n_missing
				? `: ${stat.n_missing} trial${stat.n_missing === 1 ? '' : 's'} without a value`
				: ''}"
		/>
	{:else}
		<span class="font-mono tabular-nums">{fmt(stat.mean, share)}</span>
	{/if}
	{#if detail}
		<span class="block text-[11px] whitespace-nowrap text-muted-foreground">
			{#if stat.stdev != null}±{fmt(stat.stdev, share)} ·{/if}
			n={stat.n}{stat.n_missing ? ` · ${stat.n_missing} missing` : ''}
		</span>
	{/if}
{/if}
