<script lang="ts">
	/**
	 * An analysis plan hash: short form, full hash on hover, copy button and a
	 * link to the runs measured under it (provenance of every number).
	 */
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import { planRunsHref, shortHash } from './format';

	interface Props {
		hash: string;
		/** Link the hash to the runs list filtered to this plan. */
		link?: boolean;
		copy?: boolean;
		class?: string;
	}

	let { hash, link = true, copy = true, class: className }: Props = $props();

	let copied = $state(false);

	async function copyHash() {
		try {
			await navigator.clipboard.writeText(hash);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			toast.error('Could not copy to the clipboard.');
		}
	}
</script>

<span class={cn('inline-flex min-w-0 items-center gap-1', className)}>
	{#if link}
		<a
			href={planRunsHref(hash)}
			class="mono-tag hover:border-primary/50 hover:text-foreground transition-colors"
			title="Plan {hash} — open the runs measured under this plan"
		>
			{shortHash(hash)}
		</a>
	{:else}
		<span class="mono-tag" title="Plan {hash}">{shortHash(hash)}</span>
	{/if}
	{#if copy}
		<button
			type="button"
			class="inline-flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
			onclick={copyHash}
			aria-label={copied ? 'Plan hash copied' : 'Copy the full plan hash'}
			title="Copy the full plan hash"
		>
			{#if copied}
				<Check class="h-3.5 w-3.5 text-success-fg" />
			{:else}
				<Copy class="h-3.5 w-3.5" />
			{/if}
		</button>
	{/if}
</span>
