<script lang="ts">
	/**
	 * CopyValue — a mono value (hash, id, path) with a copy button; long
	 * values can be shortened with `display` while the full value is copied.
	 *
	 * Props: `value`, `display?` (shown text, default `value`), `label`
	 * (accessible name of what is copied, e.g. "plan hash"), `class`.
	 */
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import Copy from '@lucide/svelte/icons/copy';
	import Check from '@lucide/svelte/icons/check';

	interface Props {
		value: string;
		display?: string;
		label?: string;
		class?: string;
	}

	let { value, display, label = 'value', class: className }: Props = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		try {
			await navigator.clipboard.writeText(value);
			copied = true;
			clearTimeout(timer);
			timer = setTimeout(() => (copied = false), 1500);
		} catch {
			toast.error('Could not copy');
		}
	}
</script>

<span class={cn('inline-flex min-w-0 max-w-full items-center gap-1', className)}>
	<span class="min-w-0 truncate font-mono text-xs" title={value}>{display ?? value}</span>
	{#if value}
		<button
			type="button"
			class="inline-flex size-5 shrink-0 cursor-pointer items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
			onclick={copy}
			aria-label="Copy {label}"
		>
			{#if copied}
				<Check class="size-3 text-success-fg" aria-hidden="true" />
			{:else}
				<Copy class="size-3" aria-hidden="true" />
			{/if}
		</button>
	{/if}
</span>
