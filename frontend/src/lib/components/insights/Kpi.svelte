<script lang="ts">
	/**
	 * A KPI tile whose value may be missing: `value` null → "—" with `reason`.
	 *
	 *   <Kpi label="Static findings" value={fmtInt(d.total_findings)} reason="Nothing measured" sub="counted" />
	 */
	import { cn } from '$lib/utils';
	import NaValue from '$lib/components/analysis/NaValue.svelte';

	interface Props {
		label: string;
		value: string | null | undefined;
		reason?: string;
		sub?: string;
		/** De-emphasised value (e.g. AI opinions next to measurements). */
		muted?: boolean;
		class?: string;
	}

	let {
		label,
		value,
		reason = 'Not available',
		sub,
		muted = false,
		class: className
	}: Props = $props();
</script>

<div class={cn('kpi-card', className)}>
	<div class="kpi-label">{label}</div>
	<div class="kpi-value {muted ? 'text-muted-foreground' : ''}">
		{#if value == null || value === ''}<NaValue {reason} />{:else}{value}{/if}
	</div>
	{#if sub}<div class="text-xs text-muted-foreground">{sub}</div>{/if}
</div>
