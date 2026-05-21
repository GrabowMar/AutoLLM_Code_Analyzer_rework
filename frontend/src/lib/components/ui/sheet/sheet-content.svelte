<script lang="ts">
	import { Dialog } from 'bits-ui';
	import { cn } from '$lib/utils';
	import { fly } from 'svelte/transition';
	import X from '@lucide/svelte/icons/x';
	import SheetOverlay from './sheet-overlay.svelte';

	let {
		class: className,
		side = 'left',
		children,
		showClose = true,
		...restProps
	}: Dialog.ContentProps & {
		class?: string;
		side?: 'top' | 'bottom' | 'left' | 'right';
		children?: import('svelte').Snippet;
		showClose?: boolean;
	} = $props();

	const flyParams: Record<string, { x?: number; y?: number }> = {
		top: { y: -300 },
		bottom: { y: 300 },
		left: { x: -300 },
		right: { x: 300 }
	};

	const sideClasses: Record<string, string> = {
		top: 'inset-x-0 top-0 border-b',
		bottom: 'inset-x-0 bottom-0 border-t',
		left: 'inset-y-0 left-0 h-full w-3/4 max-w-sm border-r',
		right: 'inset-y-0 right-0 h-full w-3/4 max-w-sm border-l'
	};
</script>

<Dialog.Portal>
	<SheetOverlay />
	<Dialog.Content
		transition={fly}
		transitionConfig={{ ...flyParams[side], duration: 250 }}
		class={cn(
			'fixed z-50 flex flex-col gap-4 bg-background p-6 shadow-lg',
			sideClasses[side],
			className
		)}
		{...restProps}
	>
		{#if children}{@render children()}{/if}
		{#if showClose}
			<Dialog.Close
				class="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
			>
				<X class="h-4 w-4" />
				<span class="sr-only">Close</span>
			</Dialog.Close>
		{/if}
	</Dialog.Content>
</Dialog.Portal>
