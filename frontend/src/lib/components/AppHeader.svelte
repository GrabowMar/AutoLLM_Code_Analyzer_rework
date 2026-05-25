<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PanelLeft from '@lucide/svelte/icons/panel-left';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import User from '@lucide/svelte/icons/user';
	import UserCog from '@lucide/svelte/icons/user-cog';
	import Palette from '@lucide/svelte/icons/palette';
	import Cookie from '@lucide/svelte/icons/cookie';
	import Key from '@lucide/svelte/icons/key';
	import LogOut from '@lucide/svelte/icons/log-out';
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
	import X from '@lucide/svelte/icons/x';
	import { onMount } from 'svelte';
	import { Separator } from '$lib/components/ui/separator';
	import * as Sheet from '$lib/components/ui/sheet';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import { getMe } from '$lib/api/client';
	import { cn } from '$lib/utils';
	import type { Component } from 'svelte';

	interface AuthState {
		isAuthenticated: boolean;
		user: { id: number; email: string; display?: string; name?: string } | null;
	}

	interface NavItem {
		label: string;
		href: string;
		icon: Component;
	}

	interface NavSection {
		title: string;
		items: NavItem[];
		staffOnly?: boolean;
	}

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
				{ label: 'Templates', href: '/templates', icon: Layers },
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
			items: [
				{ label: 'System', href: '/system', icon: Activity },
			],
		},
	];

	function isActive(href: string): boolean {
		const pathname = page.url?.pathname ?? '/';
		if (href === '/') return pathname === '/';
		return pathname === href || pathname.startsWith(href + '/');
	}

	let { auth }: { auth: AuthState } = $props();

	const authStore = getAuth();
	const preferences = getPreferences();

	let isStaff = $state(false);
	let mobileMenuOpen = $state(false);
	let dropdownOpen = $state(false);

	onMount(async () => {
		try {
			const me = await getMe();
			isStaff = me.is_staff ?? false;
		} catch {
			isStaff = false;
		}
	});

	const avatarColorMap: Record<string, string> = {
		blue: 'bg-blue-500',
		indigo: 'bg-indigo-500',
		purple: 'bg-purple-500',
		pink: 'bg-pink-500',
		red: 'bg-red-500',
		orange: 'bg-orange-500',
		amber: 'bg-amber-500',
		green: 'bg-green-500',
		teal: 'bg-teal-500',
		cyan: 'bg-cyan-500'
	};

	const routeLabels: Record<string, string> = {
		'/': 'Dashboard',
		'/models': 'Models',
		'/applications': 'Applications',
		'/analysis': 'Analysis',
		'/reports': 'Reports',
		'/rankings': 'Rankings',
		'/statistics': 'Statistics',
		'/sample-generator': 'Sample Generator',
		'/templates': 'Templates',
		'/automation': 'Automation',
		'/runtime': 'Runtime',
		'/system': 'System',
		'/docs': 'Docs',
		'/about': 'About',
		'/privacy': 'Privacy Policy',
		'/users': 'Users',
		'/users/settings': 'Settings'
	};

	let breadcrumbSegments = $derived.by(() => {
		const pathname = page.url?.pathname ?? '/';
		if (pathname === '/') return [{ label: 'Dashboard', href: '/' }];

		const parts = pathname.split('/').filter(Boolean);
		const segments: { label: string; href: string }[] = [];

		let accumulated = '';
		for (const part of parts) {
			accumulated += '/' + part;
			const label = routeLabels[accumulated];
			if (label) {
				segments.push({ label, href: accumulated });
			} else if (accumulated.startsWith('/users/') && accumulated !== '/users/settings') {
				// /users/<pk> — render as "Profile"
				segments.push({ label: 'Profile', href: accumulated });
			}
		}

		if (segments.length === 0) {
			const firstLevel = '/' + parts[0];
			const label = routeLabels[firstLevel];
			if (label) {
				segments.push({ label, href: firstLevel });
			}
		}

		if (segments.length === 0) {
			segments.push({ label: parts[parts.length - 1], href: pathname });
		}

		return segments;
	});

	let avatarBgClass = $derived(avatarColorMap[preferences.avatarColor] ?? 'bg-blue-500');

	let userInitial = $derived.by(() => {
		if (!auth.user) return '?';
		const name = auth.user.display || auth.user.name || auth.user.email;
		return name.charAt(0).toUpperCase();
	});

	let userDisplayName = $derived.by(() => {
		if (!auth.user) return '';
		return auth.user.display || auth.user.name || auth.user.email;
	});

	function toggleDropdown() {
		dropdownOpen = !dropdownOpen;
	}

	function closeDropdown() {
		dropdownOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (dropdownOpen && !target.closest('[data-user-menu]')) {
			closeDropdown();
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && dropdownOpen) {
			closeDropdown();
		}
	}

	async function handleLogout() {
		closeDropdown();
		await authStore.logout();
		goto('/auth/login');
	}
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleKeydown} />

<header
	class="fixed top-0 left-0 right-0 z-30 flex h-12 items-center gap-3 border-b border-border/70 bg-background/85 px-3 backdrop-blur-md supports-[backdrop-filter]:bg-background/65 sm:px-4 md:gap-4 md:px-5 transition-[padding] duration-200 ease-out motion-reduce:transition-none md:pl-[calc(var(--app-sidebar-offset,0px)+1.25rem)]"
>
	<!-- Mobile sidebar trigger -->
	<Sheet.Root bind:open={mobileMenuOpen}>
		<Sheet.Trigger
			class="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-accent-foreground md:hidden cursor-pointer transition-colors"
		>
			<PanelLeft class="h-5 w-5" />
			<span class="sr-only">Toggle menu</span>
		</Sheet.Trigger>
		<Sheet.Content side="left" class="w-72 p-0 bg-sidebar text-sidebar-foreground border-sidebar-border" showClose={false}>
			<!-- Mobile nav header -->
			<div class="flex h-12 items-center justify-between border-b border-sidebar-border px-3">
				<a href="/" class="flex items-center gap-2" onclick={() => (mobileMenuOpen = false)}>
					<div class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
						<FlaskConical class="h-3.5 w-3.5" />
					</div>
					<span class="font-semibold text-sm" style="font-family: var(--font-display);">LLM<span class="text-sidebar-primary">_</span>Lab</span>
				</a>
				<button
					class="inline-flex h-9 w-9 items-center justify-center rounded-md text-sidebar-foreground/60 hover:bg-sidebar-accent hover:text-sidebar-foreground cursor-pointer transition-colors"
					onclick={() => (mobileMenuOpen = false)}
				>
					<X class="h-4 w-4" />
				</button>
			</div>

			<!-- Mobile nav sections -->
			<nav class="flex-1 overflow-y-auto py-2">
				{#each navSections as section (section.title)}
					{#if !section.staffOnly || isStaff}
						<div class="px-3 pt-3 pb-1">
							<span class="text-[10px] font-semibold uppercase tracking-[0.12em] text-sidebar-foreground/35" style="font-family: var(--font-mono);">
								{section.title}
							</span>
						</div>
						<div class="flex flex-col gap-px px-2">
							{#each section.items as item (item.href)}
								<a
									href={item.href}
									class={cn(
										'group relative flex items-center gap-2.5 rounded px-2.5 py-2 text-[13px] transition-colors duration-150 motion-reduce:transition-none',
										isActive(item.href)
											? 'bg-sidebar-accent text-sidebar-primary font-medium'
											: 'text-sidebar-foreground/65 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'
									)}
									onclick={() => (mobileMenuOpen = false)}
								>
									{#if isActive(item.href)}
										<span class="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-0.5 rounded-r bg-sidebar-primary"></span>
									{/if}
									<item.icon class="h-3.5 w-3.5 shrink-0" />
									{item.label}
								</a>
							{/each}
						</div>
					{/if}
				{/each}
			</nav>

			<!-- Mobile nav footer -->
			<div class="border-t border-sidebar-border px-3 py-2.5">
				<div class="flex items-center justify-between">
					<div class="flex items-center gap-2">
						<div class="relative flex h-1.5 w-1.5 shrink-0">
							<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-[color:var(--success)] opacity-60"></span>
							<span class="relative inline-flex h-1.5 w-1.5 rounded-full bg-[color:var(--success)]"></span>
						</div>
						<span class="text-[11px] text-sidebar-foreground/70">System online</span>
					</div>
					<ThemeToggle />
				</div>
			</div>
		</Sheet.Content>
	</Sheet.Root>

	<a href="/" class="flex items-center gap-2 font-semibold text-sm min-w-0" style="font-family: var(--font-display);">
		<FlaskConical class="h-4 w-4 text-primary shrink-0" />
		<span class="truncate">LLM<span class="text-primary">_</span>Lab</span>
	</a>

	{#if breadcrumbSegments.length > 0}
		<nav aria-label="Breadcrumb" class="hidden items-center gap-1 text-xs text-muted-foreground md:flex touch-mobile-hide-nav" style="font-family: var(--font-mono);">
			<span class="text-muted-foreground/40">/</span>
			{#each breadcrumbSegments as segment, i}
				{#if i > 0}
					<span class="text-muted-foreground/40">/</span>
				{/if}
				{#if i === breadcrumbSegments.length - 1}
					<span class="font-medium text-foreground">{segment.label}</span>
				{:else}
					<a href={segment.href} class="hover:text-primary transition-colors">{segment.label}</a>
				{/if}
			{/each}
		</nav>
	{/if}

	<div class="flex-1"></div>

	<div class="flex items-center gap-1.5">
		<!-- System status dot (desktop) -->
		<div
			class="hidden md:flex items-center gap-1.5 rounded-md border border-border/60 bg-muted/30 px-2 py-1 text-[10px] text-muted-foreground"
			style="font-family: var(--font-mono);"
			title="System online"
		>
			<span class="relative flex h-1.5 w-1.5 shrink-0">
				<span class="absolute inline-flex h-full w-full animate-ping rounded-full bg-[color:var(--success)] opacity-60 motion-reduce:hidden"></span>
				<span class="relative inline-flex h-1.5 w-1.5 rounded-full bg-[color:var(--success)]"></span>
			</span>
			<span class="uppercase tracking-wider">online</span>
		</div>

		<ThemeToggle />

		{#if auth.isAuthenticated && auth.user}
			<Separator orientation="vertical" class="mx-1 h-5 bg-border/70" />

			<div class="relative" data-user-menu>
				<button
					class="flex items-center gap-2 rounded-md p-0.5 hover:ring-2 hover:ring-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-all cursor-pointer"
					onclick={toggleDropdown}
					aria-expanded={dropdownOpen}
					aria-haspopup="true"
				>
					<div
						class="{avatarBgClass} flex h-7 w-7 items-center justify-center rounded-md text-xs font-semibold text-white shadow-sm"
						style="font-family: var(--font-mono);"
					>
						{userInitial}
					</div>
				</button>

				{#if dropdownOpen}
					<div
						class="absolute right-0 top-full mt-1.5 w-64 overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-xl"
						role="menu"
					>
						<!-- User identity header -->
						<div class="flex items-center gap-3 border-b border-border/60 bg-muted/30 px-3 py-2.5">
							<div
								class="{avatarBgClass} flex h-9 w-9 shrink-0 items-center justify-center rounded-md text-sm font-semibold text-white shadow-sm"
								style="font-family: var(--font-mono);"
							>
								{userInitial}
							</div>
							<div class="min-w-0 flex-1">
								<p class="text-sm font-medium truncate">{userDisplayName}</p>
								<p class="text-[11px] text-muted-foreground truncate" style="font-family: var(--font-mono);">
									{auth.user.email}
								</p>
							</div>
						</div>

						<div class="p-1">
							<p class="px-2 pt-1.5 pb-1 text-[10px] uppercase tracking-[0.14em] text-muted-foreground/70" style="font-family: var(--font-mono);">
								Profile
							</p>
							<a
								href={`/users/${auth.user.id}`}
								class="flex cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
								role="menuitem"
								onclick={closeDropdown}
							>
								<User class="h-3.5 w-3.5" />
								<span class="flex-1">View profile</span>
								<span class="text-[10px] text-muted-foreground/70" style="font-family: var(--font-mono);">↗</span>
							</a>
							<a
								href="/users/settings#account"
								class="flex cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
								role="menuitem"
								onclick={closeDropdown}
							>
								<UserCog class="h-3.5 w-3.5" />
								Account settings
							</a>

							<div role="separator" class="my-1 h-px bg-border/60"></div>
							<p class="px-2 pt-0.5 pb-1 text-[10px] uppercase tracking-[0.14em] text-muted-foreground/70" style="font-family: var(--font-mono);">
								Preferences
							</p>
							<a
								href="/users/settings#appearance"
								class="flex cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
								role="menuitem"
								onclick={closeDropdown}
							>
								<Palette class="h-3.5 w-3.5" />
								Appearance
							</a>
							<a
								href="/users/settings#privacy"
								class="flex cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
								role="menuitem"
								onclick={closeDropdown}
							>
								<Cookie class="h-3.5 w-3.5" />
								Privacy &amp; cookies
							</a>
							<a
								href="/users/settings#credentials"
								class="flex cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm hover:bg-accent hover:text-accent-foreground transition-colors"
								role="menuitem"
								onclick={closeDropdown}
							>
								<Key class="h-3.5 w-3.5" />
								API credentials
							</a>

							<div role="separator" class="my-1 h-px bg-border/60"></div>

							<button
								class="flex w-full cursor-pointer items-center gap-2 rounded px-2.5 py-1.5 text-sm text-destructive hover:bg-[color:var(--destructive)]/10 transition-colors"
								role="menuitem"
								onclick={handleLogout}
							>
								<LogOut class="h-3.5 w-3.5" />
								Sign out
							</button>
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</header>
