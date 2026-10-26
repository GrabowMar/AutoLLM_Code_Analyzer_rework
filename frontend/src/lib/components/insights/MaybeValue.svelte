<script lang="ts">
	/**
	 * A formatted value, or `NaValue` when the formatter returned null.
	 *
	 *   <MaybeValue value={fmtShare(rate)} reason="No finished job" />
	 */
	import NaValue from '$lib/components/analysis/NaValue.svelte';

	interface Props {
		value: string | null | undefined;
		reason: string;
		class?: string;
	}

	let { value, reason, class: className }: Props = $props();
</script>

{#if value == null || value === ''}<NaValue {reason} class={className} />{:else}<span
		class={className}>{value}</span
	>{/if}
