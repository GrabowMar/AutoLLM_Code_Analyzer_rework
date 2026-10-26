<script lang="ts">
	/** CopyField — a one-line read-only value (URL, ID) with a Copy button. */
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';

	let { value, label, class: className }: { value: string; label: string; class?: string } = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		try {
			await navigator.clipboard.writeText(value);
			copied = true;
			clearTimeout(timer);
			timer = setTimeout(() => (copied = false), 1500);
		} catch {
			toast.error('Could not copy.');
		}
	}
</script>

<div class={cn('flex h-9 min-w-0 items-center rounded-md border border-input bg-muted/40', className)}>
	<code class="min-w-0 flex-1 truncate px-3 font-mono text-xs" title={value} aria-label={label}>{value}</code>
	<button
		type="button"
		class="flex h-full shrink-0 cursor-pointer items-center gap-1.5 border-l border-input px-3 text-xs font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
		onclick={copy}
		aria-label="Copy {label}"
	>
		{#if copied}<Check class="size-3.5 text-success" aria-hidden="true" />Copied{:else}<Copy
				class="size-3.5"
				aria-hidden="true"
			/>Copy{/if}
	</button>
</div>
