<script lang="ts">
	/**
	 * CopyBlock — a monospace block (terminal surface) with a Copy button.
	 *
	 * Props: `text`, `label` (accessible name of the block and of the button,
	 * e.g. "Python install commands"), `wrap` (break long lines; default false =
	 * scroll sideways), `class`.
	 */
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';

	interface Props {
		text: string;
		label: string;
		wrap?: boolean;
		class?: string;
	}

	let { text, label, wrap = false, class: className }: Props = $props();

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		try {
			await navigator.clipboard.writeText(text);
			copied = true;
			clearTimeout(timer);
			timer = setTimeout(() => (copied = false), 1_500);
		} catch {
			toast.error('Could not copy. Select the text and copy it manually.');
		}
	}

	$effect(() => () => clearTimeout(timer));
</script>

<div class={cn('relative min-w-0', className)}>
	<pre
		class={cn('terminal m-0 pr-12', wrap ? 'whitespace-pre-wrap break-all' : 'whitespace-pre')}
		aria-label={label}>{text}</pre>
	<Button
		variant="ghost"
		size="sm"
		class="absolute right-1.5 top-1.5 h-7 px-2"
		aria-label={copied ? 'Copied' : `Copy ${label}`}
		title={copied ? 'Copied' : 'Copy'}
		onclick={copy}
	>
		{#if copied}<Check class="text-success-fg" />{:else}<Copy />{/if}
	</Button>
</div>
