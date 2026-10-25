<script lang="ts">
	import { avatarColorClasses } from '$lib/constants/colors';
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
	import X from '@lucide/svelte/icons/x';
	import SidebarNav from './SidebarNav.svelte';
	import { routeLabels } from '$lib/nav';
	import { Separator } from '$lib/components/ui/separator';
	import * as Sheet from '$lib/components/ui/sheet';
	import ThemeToggle from '$lib/components/ThemeToggle.svelte';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import { cn } from '$lib/utils';

	interface AuthState {
		isAuthenticated: boolean;
		user: { id: number; email: string; display?: string; name?: string } | null;
	}

	let { auth }: { auth: AuthState } = $props();

	const authStore = getAuth();
	const preferences = getPreferences();

	let mobileMenuOpen = $state(false);
	let dropdownOpen = $state(false);

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
				segments.push({ label, href: accumulated === '/experiments' ? '/automation?type=experiment' : accumulated });
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

	let avatarBgClass = $derived(avatarColorClasses[preferences.avatarColor] ?? 'bg-blue-500');

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
	class="fixed inset-x-0 top-0 z-30 flex h-12 items-center gap-3 border-b border-border bg-background/90 px-3 backdrop-blur-md transition-[padding] duration-200 ease-out motion-reduce:transition-none sm:px-4 md:gap-4 md:px-5 md:pl-[calc(var(--app-sidebar-offset,0px)+1.25rem)]"
>
	<Sheet.Root bind:open={mobileMenuOpen}>
		<Sheet.Trigger
			class="inline-flex h-9 w-9 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground md:hidden"
		>
			<PanelLeft class="h-5 w-5" />
			<span class="sr-only">Open menu</span>
		</Sheet.Trigger>
		<Sheet.Content side="left" class="w-72 gap-0 border-sidebar-border bg-sidebar p-0 text-sidebar-foreground" showClose={false}>
			<div class="flex h-12 items-center justify-between border-b border-sidebar-border px-3">
				<a href="/" class="flex items-center gap-2" onclick={() => (mobileMenuOpen = false)}>
					<span class="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-sidebar-primary text-sidebar-primary-foreground">
						<FlaskConical class="h-3.5 w-3.5" />
					</span>
					<span class="text-sm font-semibold font-display">LLM<span class="text-sidebar-primary">_</span>Lab</span>
				</a>
				<button
					class="inline-flex h-9 w-9 items-center justify-center rounded-md text-sidebar-foreground/60 transition-colors hover:bg-sidebar-accent hover:text-sidebar-foreground"
					onclick={() => (mobileMenuOpen = false)}
					aria-label="Close menu"
				>
					<X class="h-4 w-4" />
				</button>
			</div>

			<SidebarNav onnavigate={() => (mobileMenuOpen = false)} />

			<div class="flex justify-end border-t border-sidebar-border px-3 py-2">
				<ThemeToggle />
			</div>
		</Sheet.Content>
	</Sheet.Root>

	<a href="/" class="flex min-w-0 items-center gap-2 text-sm font-semibold font-display md:hidden">
		<FlaskConical class="h-4 w-4 shrink-0 text-primary" />
		<span class="truncate">LLM<span class="text-primary">_</span>Lab</span>
	</a>

	{#if breadcrumbSegments.length > 0}
		<nav aria-label="Breadcrumb" class="hidden min-w-0 md:block">
			<ol class="flex items-center gap-1.5 text-sm text-muted-foreground">
				{#each breadcrumbSegments as segment, i (segment.href)}
					{#if i > 0}
						<li aria-hidden="true" class="text-muted-foreground/50">/</li>
					{/if}
					<li class="min-w-0 truncate">
						{#if i === breadcrumbSegments.length - 1}
							<span class="font-medium text-foreground" aria-current="page">{segment.label}</span>
						{:else}
							<a href={segment.href} class="transition-colors hover:text-foreground">{segment.label}</a>
						{/if}
					</li>
				{/each}
			</ol>
		</nav>
	{/if}

	<div class="flex-1"></div>

	<div class="flex items-center gap-1.5">
		<ThemeToggle />

		{#if auth.isAuthenticated && auth.user}
			<Separator orientation="vertical" class="mx-1 h-5 bg-border/70" />

			<div class="relative" data-user-menu>
				<button
					class="flex items-center gap-2 rounded-md p-0.5 hover:ring-2 hover:ring-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-all cursor-pointer"
					onclick={toggleDropdown}
					aria-expanded={dropdownOpen}
					aria-haspopup="true"
					aria-label="Account menu"
				>
					<div
						class="{avatarBgClass} flex h-7 w-7 items-center justify-center rounded-md text-xs font-semibold text-white shadow-sm"
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
							>
								{userInitial}
							</div>
							<div class="min-w-0 flex-1">
								<p class="text-sm font-medium truncate">{userDisplayName}</p>
								<p class="text-[11px] text-muted-foreground truncate">
									{auth.user.email}
								</p>
							</div>
						</div>

						<div class="p-1">
							<p class="px-2 pt-1.5 pb-1 text-[11px] uppercase tracking-[0.14em] text-muted-foreground/70">
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
								<span class="text-[11px] text-muted-foreground/70">↗</span>
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
							<p class="px-2 pt-0.5 pb-1 text-[11px] uppercase tracking-[0.14em] text-muted-foreground/70">
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
