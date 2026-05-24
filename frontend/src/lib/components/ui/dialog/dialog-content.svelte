<script lang="ts">
import { Dialog } from 'bits-ui';
import { cn } from '$lib/utils';
import X from '@lucide/svelte/icons/x';
import DialogOverlay from './dialog-overlay.svelte';

let {
	class: className,
	children,
	showClose = true,
	...restProps
}: Dialog.ContentProps & {
	class?: string;
	children?: import('svelte').Snippet;
	showClose?: boolean;
} = $props();
</script>

<Dialog.Portal>
	<DialogOverlay />
	<Dialog.Content
		class={cn(
			'fixed left-[50%] top-[50%] z-50 translate-x-[-50%] translate-y-[-50%] rounded-lg border bg-background shadow-lg',
			'w-full max-w-lg p-6',
			className
		)}
		{...restProps}
	>
		{#if children}{@render children()}{/if}
		{#if showClose}
			<Dialog.Close
				class="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring"
			>
				<X class="h-4 w-4" />
				<span class="sr-only">Close</span>
			</Dialog.Close>
		{/if}
	</Dialog.Content>
</Dialog.Portal>
