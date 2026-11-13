<script lang="ts">
	/**
	 * Highlight — `text` with the search words marked (pick lists). The mark keeps the text colour on a
	 * primary tint, so it stays ≥ 4.5:1 on selected and hovered rows too.
	 *
	 * Props: `text`, `tokens` (lower-case words, `searchTokens(query)` from `$lib/utils/search`).
	 */
	import { highlightParts } from '$lib/utils/search';

	let { text, tokens }: { text: string; tokens: string[] } = $props();
</script>

{#each highlightParts(text, tokens) as part, i (i)}{#if part.match}<mark class="hl">{part.text}</mark>{:else}{part.text}{/if}{/each}

<style>
	.hl {
		border-radius: 2px;
		background: color-mix(in oklab, var(--primary) 20%, transparent);
		padding-inline: 1px;
		color: var(--foreground);
	}
</style>
