<script lang="ts">
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Boxes from '@lucide/svelte/icons/boxes';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import FileText from '@lucide/svelte/icons/file-text';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';
	import Trophy from '@lucide/svelte/icons/trophy';
	import ChartColumn from '@lucide/svelte/icons/chart-column';
	import BookOpen from '@lucide/svelte/icons/book-open';
	import Zap from '@lucide/svelte/icons/zap';
	import Layers from '@lucide/svelte/icons/layers';
	import Container from '@lucide/svelte/icons/container';
	import Activity from '@lucide/svelte/icons/activity';
	import ChevronsLeft from '@lucide/svelte/icons/chevrons-left';
	import ChevronsRight from '@lucide/svelte/icons/chevrons-right';
	import Circle from '@lucide/svelte/icons/circle';
	import { page } from '$app/state';
	import { cn } from '$lib/utils';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import { getMe } from '$lib/api/client';
	import type { Component } from 'svelte';
	import { onMount } from 'svelte';

	const prefs = getPreferences();

	interface NavItem {
		label: string;
		href: string;
		icon: Component;
		staffOnly?: boolean;
		subItem?: boolean;
	}

	interface NavSection {
		title: string;
		items: NavItem[];
		staffOnly?: boolean;
	}

	let isStaff = $state(false);

	onMount(async () => {
		try {
			const me = await getMe();
			isStaff = me.is_staff ?? false;
		} catch {
			isStaff = false;
		}
	});

	const navSections: NavSection[] = [
		{
			title: 'Platform',
			items: [
				{ label: 'Dashboard', href: '/', icon: FlaskConical },
				{ label: 'Models', href: '/models', icon: Boxes },
				{ label: 'Applications', href: '/applications', icon: AppWindow },
				{ label: 'Analysis', href: '/analysis', icon: BarChart3 },
			],
		},
		{
			title: 'Tools',
			items: [
				{ label: 'Automation', href: '/automation', icon: Zap },
				{ label: 'Sample Generator', href: '/sample-generator', icon: WandSparkles },
				{ label: 'Templates', href: '/sample-generator/templates', icon: Layers },
				{ label: 'Reports', href: '/reports', icon: FileText },
				{ label: 'Runtime', href: '/runtime', icon: Container },
			],
		},
		{
			title: 'Insights',
			items: [
				{ label: 'Rankings', href: '/rankings', icon: Trophy },
				{ label: 'Statistics', href: '/statistics', icon: ChartColumn },
				{ label: 'Docs', href: '/docs', icon: BookOpen },
			],
		},
		{
			title: 'Admin',
			staffOnly: true,
			items: [{ label: 'System', href: '/system', icon: Activity, staffOnly: true }],
		},
	];

	function isActive(href: string): boolean {
		if (href === '/') return page.url.pathname === '/';
		if (page.url.pathname === href) return true;
		return page.url.pathname.startsWith(href + '/') || page.url.pathname === href;
	}
</script>

<aside
	class={cn(
		'hidden fixed inset-y-0 left-0 z-40 border-r border-sidebar-border bg-sidebar md:flex md:flex-col transition-[width] duration-200 ease-out motion-reduce:transition-none touch-mobile-hide',
		prefs.sidebarCollapsed ? 'w-14' : 'w-60'
	)}
>
	<!-- Brand header -->
	<div
		class={cn(
			'flex h-12 items-center border-b border-sidebar-border',
			prefs.sidebarCollapsed ? 'justify-center px-0' : 'justify-between px-2.5'
		)}
	>
		<a
			href="/"
			class={cn(
				'flex items-center min-w-0 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring',
				prefs.sidebarCollapsed ? 'justify-center' : 'gap-2'
			)}
			aria-label="LLM Lab — Home"
		>
			<div class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground shadow-sm">
				<FlaskConical class="h-3.5 w-3.5" />
			</div>
			{#if !prefs.sidebarCollapsed}
				<span class="font-semibold text-sm text-sidebar-foreground truncate" style="font-family: var(--font-display);">LLM<span class="text-sidebar-primary">_</span>Lab</span>
			{/if}
		</a>
		{#if !prefs.sidebarCollapsed}
			<button
				onclick={() => prefs.toggleSidebar()}
				class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-sidebar-foreground/40 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				title="Collapse sidebar"
				aria-label="Collapse sidebar"
			>
				<ChevronsLeft class="h-3.5 w-3.5" />
			</button>
		{/if}
	</div>

	<!-- Navigation -->
	<nav class="flex-1 min-h-0 overflow-y-auto no-scrollbar py-1.5">
		{#each navSections as section (section.title)}
		{#if !section.staffOnly || isStaff}
			{#if !prefs.sidebarCollapsed}
				<div class="px-3 pt-3 pb-1">
					<span class="text-[10px] font-semibold uppercase tracking-[0.12em] text-sidebar-foreground/35" style="font-family: var(--font-mono);">
						{section.title}
					</span>
				</div>
			{:else}
				<div class="my-2 mx-2.5">
					<div class="h-px bg-sidebar-foreground/10"></div>
				</div>
			{/if}

			<div class={cn('flex flex-col gap-px', prefs.sidebarCollapsed ? 'px-1.5' : 'px-2')}>
				{#each section.items as item (item.href)}
					<a
						href={item.href}
						class={cn(
							'group relative flex items-center rounded text-[13px] transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
							prefs.sidebarCollapsed ? 'justify-center px-0 py-2' : 'gap-2.5 px-2.5 py-1.5',
							!prefs.sidebarCollapsed && item.subItem ? 'ml-4 text-xs' : '',
							isActive(item.href)
								? 'bg-sidebar-accent text-sidebar-primary font-medium'
								: 'text-sidebar-foreground/65 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'
						)}
						title={prefs.sidebarCollapsed ? item.label : undefined}
					>
						{#if isActive(item.href)}
							<span class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-r bg-sidebar-primary"></span>
						{/if}
						<item.icon class="h-3.5 w-3.5 shrink-0" />
						{#if !prefs.sidebarCollapsed}
							{item.label}
						{/if}
						{#if prefs.sidebarCollapsed}
							<div class="pointer-events-none absolute left-full ml-2 hidden rounded border border-border bg-popover px-2 py-1 text-xs font-medium text-popover-foreground shadow-lg group-hover:block z-50 whitespace-nowrap" style="font-family: var(--font-mono);">
								{item.label}
							</div>
						{/if}
					</a>
				{/each}
			</div>
		{/if}
		{/each}
	</nav>

	<!-- Status footer -->
	<div
		class={cn(
			'mt-auto border-t border-sidebar-border',
			prefs.sidebarCollapsed ? 'flex flex-col items-center gap-1.5 px-1.5 py-2' : 'px-2.5 py-2'
		)}
	>
		{#if prefs.sidebarCollapsed}
			<div
				class="relative flex h-7 w-7 items-center justify-center rounded-md border border-sidebar-border/60 bg-sidebar-accent/30"
				title="System online"
				aria-label="System online"
			>
				<span class="absolute inline-flex h-2 w-2 animate-ping rounded-full bg-[color:var(--success)] opacity-60 motion-reduce:hidden"></span>
				<span class="relative inline-flex h-2 w-2 rounded-full bg-[color:var(--success)]"></span>
			</div>
			<button
				onclick={() => prefs.toggleSidebar()}
				class="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded text-sidebar-foreground/50 hover:bg-sidebar-accent hover:text-sidebar-foreground transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				title="Expand sidebar"
				aria-label="Expand sidebar"
			>
				<ChevronsRight class="h-3.5 w-3.5" />
			</button>
		{:else}
			<div class="flex items-center justify-between gap-2">
				<div class="flex items-center gap-2 min-w-0">
					<div class="relative flex h-1.5 w-1.5 shrink-0">
						<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-[color:var(--success)] opacity-60 motion-reduce:hidden"></span>
						<span class="relative inline-flex h-1.5 w-1.5 rounded-full bg-[color:var(--success)]"></span>
					</div>
					<div class="min-w-0">
						<div class="text-[11px] font-medium text-sidebar-foreground leading-tight">System online</div>
						<div class="text-[10px] text-sidebar-foreground/40 leading-tight" style="font-family: var(--font-mono);">
							v2.0.0 · all systems go
						</div>
					</div>
				</div>
				<span
					class="inline-flex shrink-0 items-center rounded border border-sidebar-border/70 bg-sidebar-accent/40 px-1.5 py-0.5 text-[9px] uppercase tracking-[0.14em] text-sidebar-foreground/55"
					style="font-family: var(--font-mono);"
					title="Environment"
				>
					local
				</span>
			</div>
		{/if}
	</div>
</aside>
