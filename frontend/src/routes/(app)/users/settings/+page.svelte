<script lang="ts">
	import { getMe } from '$lib/api/users';
	import type { ApiUser } from '$lib/api/users';
	import { onMount, untrack } from 'svelte';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';

	import ProfileSection from '$lib/components/users-settings/ProfileSection.svelte';
	import SecuritySection from '$lib/components/users-settings/SecuritySection.svelte';
	import AppearanceSection from '$lib/components/users-settings/AppearanceSection.svelte';
	import WorkspaceSection from '$lib/components/users-settings/WorkspaceSection.svelte';
	import CookieSettingsSection from '$lib/components/users-settings/CookieSettingsSection.svelte';
	import ApiKeysSection from '$lib/components/users-settings/ApiKeysSection.svelte';
	import TokenSection from '$lib/components/users-settings/TokenSection.svelte';
	import DataManagementSection from '$lib/components/users-settings/DataManagementSection.svelte';

	import User from '@lucide/svelte/icons/user';
	import Lock from '@lucide/svelte/icons/lock';
	import Palette from '@lucide/svelte/icons/palette';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import Cookie from '@lucide/svelte/icons/cookie';
	import Key from '@lucide/svelte/icons/key';
	import Terminal from '@lucide/svelte/icons/terminal';
	import Database from '@lucide/svelte/icons/database';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import type { Component } from 'svelte';

	interface Section {
		id: string;
		label: string;
		group: 'account' | 'application' | 'privacy' | 'integrations' | 'data';
		icon: Component;
	}

	const SECTIONS: Section[] = [
		{ id: 'account', label: 'Account', group: 'account', icon: User },
		{ id: 'security', label: 'Security', group: 'account', icon: Lock },
		{ id: 'appearance', label: 'Appearance', group: 'application', icon: Palette },
		{ id: 'workspace', label: 'Workspace', group: 'application', icon: LayoutDashboard },
		{ id: 'privacy', label: 'Privacy & Cookies', group: 'privacy', icon: Cookie },
		{ id: 'credentials', label: 'API Credentials', group: 'integrations', icon: Key },
		{ id: 'tokens', label: 'Personal Tokens', group: 'integrations', icon: Terminal },
		{ id: 'data', label: 'Data Management', group: 'data', icon: Database },
	];

	const GROUP_LABELS: Record<Section['group'], string> = {
		account: 'Account',
		application: 'Application',
		privacy: 'Privacy',
		integrations: 'Integrations',
		data: 'Data',
	};

	const groupedSections = $derived(
		(Object.keys(GROUP_LABELS) as Section['group'][]).map((g) => ({
			group: g,
			label: GROUP_LABELS[g],
			items: SECTIONS.filter((s) => s.group === g),
		})),
	);

	let profile = $state<ApiUser | null>(null);
	let loading = $state(true);
	let activeId = $state<string>('account');
	let showBackToTop = $state(false);

	function scrollToSection(id: string) {
		if (typeof window === 'undefined') return;
		const el = document.getElementById(`section-${id}`);
		if (!el) return;
		const headerOffset = 64; // app header (~3rem) + breathing room
		const top = el.getBoundingClientRect().top + window.scrollY - headerOffset;
		window.scrollTo({ top, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
		activeId = id;
		history.replaceState(history.state, '', `${window.location.pathname}#${id}`);
	}

	function prefersReducedMotion(): boolean {
		if (typeof window === 'undefined') return false;
		return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	}

	function scrollToTop() {
		window.scrollTo({ top: 0, behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
	}

	onMount(() => {
		// Load profile
		getMe()
			.then((u) => (profile = u))
			.catch(() => {})
			.finally(() => (loading = false));

		// Honor initial hash
		const initialHash = window.location.hash.replace('#', '');
		if (initialHash && SECTIONS.some((s) => s.id === initialHash)) {
			// Defer to next frame so layout is settled
			requestAnimationFrame(() => scrollToSection(initialHash));
		}

		// IntersectionObserver for active section tracking
		const observer = new IntersectionObserver(
			(entries) => {
				const visible = entries
					.filter((e) => e.isIntersecting)
					.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
				if (visible[0]) {
					const id = visible[0].target.id.replace('section-', '');
					untrack(() => {
						activeId = id;
					});
				}
			},
			{ rootMargin: '-80px 0px -65% 0px', threshold: [0, 0.1] },
		);
		for (const s of SECTIONS) {
			const el = document.getElementById(`section-${s.id}`);
			if (el) observer.observe(el);
		}

		const onScroll = () => {
			showBackToTop = window.scrollY > 400;
		};
		window.addEventListener('scroll', onScroll, { passive: true });

		return () => {
			observer.disconnect();
			window.removeEventListener('scroll', onScroll);
		};
	});
</script>

<svelte:head>
	<title>Settings - LLM Lab</title>
</svelte:head>

<div class="space-y-6">
	<!-- Page header (matches Dashboard / Models / Statistics) -->
	<div class="page-header">
		<div class="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
			<div class="min-w-0 space-y-1">
				<div
					class="flex items-center gap-2 text-[11px] font-medium text-muted-foreground"
					style="font-family: var(--font-mono);"
				>
					<span class="text-primary">$</span>
					<span>user_settings</span>
					<span class="text-primary" aria-hidden="true">/</span>
					<span class="truncate">{profile?.email ?? '—'}</span>
				</div>
				<h1>Settings</h1>
				<p style="font-family: var(--font-mono);">
					// configure account, appearance, workspace, privacy, and integrations
				</p>
			</div>
		</div>
	</div>

	<div class="grid gap-6 lg:grid-cols-[14rem_minmax(0,1fr)]">
		<!-- Sticky TOC sidebar (lg+) / horizontal chips (<lg) -->
		<aside class="lg:sticky lg:top-16 lg:self-start">
			<!-- Desktop nav -->
			<nav class="hidden lg:block" aria-label="Settings sections">
				<div class="space-y-4 rounded-md border border-border bg-card/40 p-3">
					{#each groupedSections as group (group.group)}
						<div class="space-y-1">
							<p
								class="px-2 text-[10px] font-medium uppercase tracking-[0.14em] text-muted-foreground"
								style="font-family: var(--font-mono);"
							>
								{group.label}
							</p>
							<ul class="space-y-0.5">
								{#each group.items as s (s.id)}
									{@const active = activeId === s.id}
									<li>
										<button
											type="button"
											onclick={() => scrollToSection(s.id)}
											class={cn(
												'group relative flex w-full items-center gap-2 rounded-sm px-2 py-1.5 text-left text-[13px] cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
												active
													? 'bg-primary/10 text-foreground'
													: 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
											)}
											aria-current={active ? 'true' : undefined}
										>
											<span
												class={cn(
													'absolute left-0 top-1 bottom-1 w-[2px] rounded-r-sm transition-opacity duration-150 motion-reduce:transition-none',
													active ? 'bg-primary opacity-100' : 'opacity-0',
												)}
												aria-hidden="true"
											></span>
											<s.icon class="h-3.5 w-3.5 shrink-0" />
											<span class="truncate">{s.label}</span>
										</button>
									</li>
								{/each}
							</ul>
						</div>
					{/each}
				</div>
			</nav>

			<!-- Mobile / tablet jump-to bar -->
			<div class="lg:hidden -mx-3 sm:mx-0">
				<div
					class="flex gap-1.5 overflow-x-auto px-3 sm:px-0 py-1 border-y border-border bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/65"
				>
					{#each SECTIONS as s (s.id)}
						{@const active = activeId === s.id}
						<button
							type="button"
							onclick={() => scrollToSection(s.id)}
							class={cn(
								'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-2.5 py-1 text-xs cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
								active
									? 'border-primary/40 bg-primary/10 text-foreground'
									: 'border-border text-muted-foreground hover:border-primary/30 hover:text-foreground',
							)}
						>
							<s.icon class="h-3 w-3" />
							{s.label}
						</button>
					{/each}
				</div>
			</div>
		</aside>

		<!-- Sections -->
		<div class="min-w-0 space-y-8">
			<!-- Account -->
			<section id="section-account" aria-labelledby="section-account-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-account-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						account
					</h2>
				</div>
				{#if loading}
					<Card.Root>
						<Card.Content class="space-y-3 pt-6 animate-pulse motion-reduce:animate-none">
							<div class="h-4 w-1/3 rounded bg-muted"></div>
							<div class="h-9 rounded bg-muted"></div>
							<div class="h-9 rounded bg-muted w-1/2"></div>
							<div class="h-9 rounded bg-muted w-32"></div>
						</Card.Content>
					</Card.Root>
				{:else}
					<ProfileSection {profile} onUpdated={(u) => (profile = u)} />
				{/if}
			</section>

			<!-- Security -->
			<section id="section-security" aria-labelledby="section-security-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-security-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						security
					</h2>
				</div>
				<SecuritySection />
			</section>

			<!-- Appearance -->
			<section id="section-appearance" aria-labelledby="section-appearance-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-appearance-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						appearance
					</h2>
				</div>
				<AppearanceSection />
			</section>

			<!-- Workspace -->
			<section id="section-workspace" aria-labelledby="section-workspace-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-workspace-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						workspace
					</h2>
				</div>
				<WorkspaceSection />
			</section>

			<!-- Privacy -->
			<section id="section-privacy" aria-labelledby="section-privacy-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-privacy-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						privacy
					</h2>
				</div>
				<CookieSettingsSection />
			</section>

			<!-- Credentials -->
			<section id="section-credentials" aria-labelledby="section-credentials-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-credentials-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						credentials
					</h2>
				</div>
				<ApiKeysSection />
			</section>

			<!-- Personal tokens -->
			<section id="section-tokens" aria-labelledby="section-tokens-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-tokens-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						personal_tokens
					</h2>
				</div>
				<TokenSection />
			</section>

			<!-- Data Management -->
			<section id="section-data" aria-labelledby="section-data-label" class="space-y-3 scroll-mt-20">
				<div class="flex items-baseline gap-2">
					<span class="text-primary" style="font-family: var(--font-mono);">//</span>
					<h2
						id="section-data-label"
						class="text-sm font-semibold uppercase tracking-[0.12em] text-muted-foreground"
						style="font-family: var(--font-mono);"
					>
						data
					</h2>
				</div>
				<DataManagementSection />
			</section>

			<div class="pt-6 text-center text-[11px] text-muted-foreground" style="font-family: var(--font-mono);">
				// end of settings · {SECTIONS.length} sections
			</div>
		</div>
	</div>
</div>

{#if showBackToTop}
	<Button
		onclick={scrollToTop}
		size="icon"
		variant="outline"
		class="fixed bottom-6 right-6 z-30 h-10 w-10 rounded-full shadow-lg motion-reduce:transition-none"
		aria-label="Back to top"
	>
		<ArrowUp class="h-4 w-4" />
	</Button>
{/if}
