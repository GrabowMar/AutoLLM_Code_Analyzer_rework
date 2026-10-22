<script lang="ts">
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import ChevronsLeft from '@lucide/svelte/icons/chevrons-left';
	import ChevronsRight from '@lucide/svelte/icons/chevrons-right';
	import { cn } from '$lib/utils';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import SidebarNav from './SidebarNav.svelte';

	const prefs = getPreferences();
</script>

<aside
	class={cn(
		'fixed inset-y-0 left-0 z-40 hidden border-r border-sidebar-border bg-sidebar transition-[width] duration-200 ease-out motion-reduce:transition-none md:flex md:flex-col',
		prefs.sidebarCollapsed ? 'w-14' : 'w-60'
	)}
>
	<div class={cn('flex h-12 items-center border-b border-sidebar-border', prefs.sidebarCollapsed ? 'justify-center' : 'px-3')}>
		<a href="/" class="flex min-w-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring" aria-label="LLM Lab — Home">
			<span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
				<FlaskConical class="h-3.5 w-3.5" />
			</span>
			{#if !prefs.sidebarCollapsed}
				<span class="truncate text-sm font-semibold text-sidebar-foreground font-display">LLM<span class="text-sidebar-primary">_</span>Lab</span>
			{/if}
		</a>
	</div>

	<SidebarNav collapsed={prefs.sidebarCollapsed} />

	<div class={cn('border-t border-sidebar-border p-1.5', prefs.sidebarCollapsed ? 'flex justify-center' : '')}>
		<button
			onclick={() => prefs.toggleSidebar()}
			class={cn(
				'inline-flex h-8 items-center gap-2 rounded text-xs text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring',
				prefs.sidebarCollapsed ? 'w-8 justify-center' : 'w-full px-2.5'
			)}
			aria-label={prefs.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
			title={prefs.sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
		>
			{#if prefs.sidebarCollapsed}
				<ChevronsRight class="h-4 w-4" />
			{:else}
				<ChevronsLeft class="h-4 w-4" />
				Collapse
			{/if}
		</button>
	</div>
</aside>
