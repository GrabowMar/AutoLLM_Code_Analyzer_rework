<script lang="ts">
	import { cn } from '$lib/utils';
	import type { Component } from 'svelte';

	export interface ProfileTab {
		id: string;
		label: string;
		icon: Component;
	}

	interface Props {
		tabs: ProfileTab[];
		active: string;
		onSelect: (id: string) => void;
	}

	let { tabs, active, onSelect }: Props = $props();

	function handleKeydown(e: KeyboardEvent) {
		const idx = tabs.findIndex((t) => t.id === active);
		if (idx < 0) return;
		if (e.key === 'ArrowRight') {
			e.preventDefault();
			onSelect(tabs[(idx + 1) % tabs.length].id);
		} else if (e.key === 'ArrowLeft') {
			e.preventDefault();
			onSelect(tabs[(idx - 1 + tabs.length) % tabs.length].id);
		}
	}
</script>

<div
	role="tablist"
	tabindex={-1}
	aria-label="Profile sections"
	onkeydown={handleKeydown}
	class="flex gap-1 overflow-x-auto border-b border-border"
>
	{#each tabs as tab (tab.id)}
		{@const isActive = tab.id === active}
		<button
			type="button"
			role="tab"
			aria-selected={isActive}
			tabindex={isActive ? 0 : -1}
			onclick={() => onSelect(tab.id)}
			class={cn(
				'group relative inline-flex items-center gap-1.5 whitespace-nowrap px-3 py-2 text-sm transition-colors cursor-pointer',
				'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-t',
				isActive ? 'text-foreground font-medium' : 'text-muted-foreground hover:text-foreground',
			)}
		>
			<tab.icon class="h-3.5 w-3.5" />
			{tab.label}
			<span
				class={cn(
					'absolute inset-x-2 -bottom-px h-0.5 rounded-full transition-all duration-200 motion-reduce:transition-none',
					isActive ? 'bg-primary opacity-100' : 'bg-primary/0 opacity-0 group-hover:bg-primary/30 group-hover:opacity-100',
				)}
			></span>
		</button>
	{/each}
</div>
