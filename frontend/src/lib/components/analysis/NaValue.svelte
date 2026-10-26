<script lang="ts">
	/**
	 * NaValue — a missing value: "—" with the reason in an accessible tooltip
	 * (keyboard-focusable, and read by screen readers). Missing data is never
	 * rendered as 0. Shared by the runs, studies and insights pages.
	 *
	 *   {#if v == null}<NaValue reason="No analysed trial" />{:else}{v}{/if}
	 *
	 * Props: `reason` (why the value is not available; a generic NA
	 * explanation when omitted), `class` (on the trigger).
	 */
	import Hint from './Hint.svelte';
	import { NA_REASON } from './run-utils';

	interface Props {
		reason?: string | null;
		class?: string;
	}

	let { reason, class: className }: Props = $props();

	const text = $derived.by(() => {
		const r = reason?.trim();
		if (!r) return NA_REASON;
		return /^not available/i.test(r) ? r : `Not available: ${r}`;
	});
</script>

<Hint {text} class={className}>
	<span class="text-muted-foreground" aria-hidden="true">—</span>
</Hint>
