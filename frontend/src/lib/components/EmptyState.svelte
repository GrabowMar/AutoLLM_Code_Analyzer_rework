<script lang="ts">
	import type { Component, Snippet } from 'svelte';
	import { cn } from '$lib/utils';
	import Inbox from '@lucide/svelte/icons/inbox';

	interface Props {
		title: string;
		description?: string;
		icon?: Component;
		/** Next step for the user, usually a Button. */
		action?: Snippet;
		/** Tighter padding for use inside cards/tables. */
		compact?: boolean;
		class?: string;
	}

	let { title, description, icon: Icon = Inbox, action, compact = false, class: className }: Props = $props();
</script>

<div class={cn('empty-state', compact && 'py-6 sm:py-8', className)}>
	<div class="empty-state-icon" aria-hidden="true"><Icon class="h-5 w-5" /></div>
	<p class="text-sm font-medium">{title}</p>
	{#if description}
		<p class="mt-1 max-w-sm text-sm text-muted-foreground">{description}</p>
	{/if}
	{#if action}
		<div class="mt-4 flex flex-wrap justify-center gap-2">{@render action()}</div>
	{/if}
</div>
