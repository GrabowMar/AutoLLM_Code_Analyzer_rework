<script lang="ts">
	/**
	 * One muted line under a card title: the values in this card come from the
	 * local model's catalog twin, assumed to be the same weights (never measured
	 * on this endpoint). Renders nothing when `from` is null.
	 */
	import Link2 from '@lucide/svelte/icons/link-2';

	interface Props {
		/** Twin model id (`deepseek/deepseek-v4-pro`). */
		from: string | null;
		/** Twin canonical slug, for the link. */
		slug?: string | null;
		/** How the link was made (`models.dev:opencode-go`, `served_name`, `manual`). */
		source?: string | null;
	}

	let { from, slug = null, source = null }: Props = $props();
</script>

{#if from}
	<p class="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground" title={source ? `Linked via ${source}` : undefined}>
		<Link2 class="size-3 shrink-0" aria-hidden="true" />
		Inherited from
		{#if slug}
			<a href="/models/{slug}" class="font-mono text-primary hover:underline">{from}</a>
		{:else}
			<span class="font-mono text-foreground">{from}</span>
		{/if}
		— assumed same weights, not measured on this endpoint.
	</p>
{/if}
