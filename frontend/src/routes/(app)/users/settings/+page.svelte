<script lang="ts">
	/**
	 * Settings — laid out like the Generation Library: a bookmark rail card on the
	 * left, and every section as one card (heading strip, then its rows flush
	 * below) in a single scrolling list. The rail (desktop) / sticky chips
	 * (mobile) follow the scroll position, every section has a shareable `#id`
	 * link, and sections report an at-a-glance status (key valid, N active
	 * tokens…) to the rail.
	 */
	import { onMount, type Component } from 'svelte';
	import { replaceState } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { Popover } from 'bits-ui';
	import { cn } from '$lib/utils';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import type { Tone } from '$lib/constants/colors';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { SETTINGS_SECTIONS, SETTINGS_GROUPS, resolveSettingsSection } from '$lib/settings-sections';
	import { provideSectionStatus } from '$lib/components/users-settings/section-status.svelte';

	import ProfileSection from '$lib/components/users-settings/ProfileSection.svelte';
	import SecuritySection from '$lib/components/users-settings/SecuritySection.svelte';
	import AppearanceSection from '$lib/components/users-settings/AppearanceSection.svelte';
	import WorkspaceSection from '$lib/components/users-settings/WorkspaceSection.svelte';
	import PrivacyDataSection from '$lib/components/users-settings/PrivacyDataSection.svelte';
	import ApiKeysSection from '$lib/components/users-settings/ApiKeysSection.svelte';
	import TokenSection from '$lib/components/users-settings/TokenSection.svelte';
	import { McpSection, ProvidersSection, GitHubSection, WebhooksSection } from '$lib/components/integrations';
	import Link from '@lucide/svelte/icons/link';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import CircleHelp from '@lucide/svelte/icons/circle-help';

	const auth = getAuth();
	const statuses = provideSectionStatus();

	const BODIES: Record<string, Component> = {
		account: ProfileSection,
		security: SecuritySection,
		appearance: AppearanceSection,
		workspace: WorkspaceSection,
		privacy: PrivacyDataSection,
		credentials: ApiKeysSection,
		providers: ProvidersSection,
		github: GitHubSection,
		mcp: McpSection,
		notifications: WebhooksSection,
		tokens: TokenSection,
	};

	const DOT: Record<Tone, string> = {
		success: 'text-success',
		warning: 'text-warning',
		danger: 'text-destructive',
		info: 'text-info',
		neutral: 'text-muted-foreground',
	};

	const ORDER = SETTINGS_SECTIONS.map((s) => s.id);
	const groups = SETTINGS_GROUPS.map((g) => ({
		...g,
		sections: SETTINGS_SECTIONS.filter((s) => s.group === g.id),
	}));

	/** A section counts as current once its top passes this line (app bar + chip bar + air). */
	const SPY_LINE = 132;

	let activeId = $state(ORDER[0]);
	let chipBar = $state<HTMLElement | null>(null);
	/** Set by a bookmark click: keeps that section current while the page settles at the bottom. */
	let pinned: string | null = null;
	let programmaticUntil = 0;
	/** Target of the last bookmark scroll, re-checked when scrolling ends (layout may have shifted meanwhile). */
	let pendingTarget: string | null = null;

	function sectionTop(id: string): number | null {
		const el = document.getElementById(id);
		return el ? el.getBoundingClientRect().top : null;
	}

	function setActive(id: string) {
		if (id === activeId) return;
		activeId = id;
		// Keep the address bar bookmarkable without adding history entries.
		replaceState(`#${id}`, {});
	}

	function spy() {
		if (performance.now() < programmaticUntil) return;
		const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
		if (atBottom && pinned) {
			const top = sectionTop(pinned);
			if (top != null && top < window.innerHeight) return setActive(pinned);
		}
		if (!atBottom) pinned = null;
		let current = ORDER[0];
		for (const id of ORDER) {
			const top = sectionTop(id);
			if (top != null && top - SPY_LINE <= 0) current = id;
		}
		if (atBottom && !pinned) current = ORDER[ORDER.length - 1];
		setActive(current);
	}

	function targetOffset(el: HTMLElement): number {
		return el.getBoundingClientRect().top - (parseFloat(getComputedStyle(el).scrollMarginTop) || 0);
	}

	function scrollToSection(id: string, behavior: ScrollBehavior = 'smooth') {
		const el = document.getElementById(id);
		if (!el) return;
		const top = targetOffset(el) + window.scrollY;
		pinned = id;
		pendingTarget = behavior === 'smooth' ? id : null;
		programmaticUntil = performance.now() + (behavior === 'smooth' ? 900 : 50);
		window.scrollTo({ top, behavior });
		setActive(id);
	}

	function jump(e: MouseEvent, id: string) {
		if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
		e.preventDefault();
		scrollToSection(id);
		document.getElementById(`${id}-title`)?.focus({ preventScroll: true });
	}

	async function copyLink(id: string, label: string) {
		const url = `${location.origin}${location.pathname}#${id}`;
		try {
			await navigator.clipboard.writeText(url);
			toast.success(`Link to ${label} copied.`);
		} catch {
			toast.error('Could not copy the link.');
		}
	}

	// Keep the active chip visible in the mobile bar.
	$effect(() => {
		const chip = chipBar?.querySelector<HTMLElement>(`[data-chip="${activeId}"]`);
		if (!chipBar || !chip) return;
		const left = chip.offsetLeft - (chipBar.clientWidth - chip.offsetWidth) / 2;
		chipBar.scrollTo({ left: Math.max(0, left), behavior: 'smooth' });
	});

	onMount(() => {
		let frame = 0;
		const onScroll = () => {
			cancelAnimationFrame(frame);
			frame = requestAnimationFrame(spy);
		};
		window.addEventListener('scroll', onScroll, { passive: true });
		const onScrollEnd = () => {
			const el = pendingTarget ? document.getElementById(pendingTarget) : null;
			pendingTarget = null;
			const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
			if (el && !atBottom && Math.abs(targetOffset(el)) > 8) {
				window.scrollTo({ top: targetOffset(el) + window.scrollY, behavior: 'instant' });
			}
			programmaticUntil = 0;
			spy();
		};
		window.addEventListener('scrollend', onScrollEnd);
		// In-page links such as <a href="#appearance"> inside a section body.
		const onHashChange = () => {
			const id = resolveSettingsSection(location.hash.slice(1));
			if (id !== activeId || Math.abs(sectionTop(id) ?? 0) > SPY_LINE) scrollToSection(id);
		};
		window.addEventListener('hashchange', onHashChange);

		// Deep link: sections load their data after mount and grow, so re-align to the
		// target until the layout settles or the user takes over.
		const hash = location.hash.slice(1);
		let settle: ResizeObserver | null = null;
		const stopSettling = () => {
			settle?.disconnect();
			settle = null;
		};
		if (hash) {
			const target = resolveSettingsSection(hash);
			requestAnimationFrame(() => scrollToSection(target, 'instant'));
			const body = document.getElementById('settings-body');
			if (body) {
				settle = new ResizeObserver(() => scrollToSection(target, 'instant'));
				settle.observe(body);
			}
			setTimeout(stopSettling, 2500);
			for (const ev of ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const) {
				window.addEventListener(ev, stopSettling, { once: true, passive: true });
			}
		} else {
			spy();
		}

		return () => {
			cancelAnimationFrame(frame);
			stopSettling();
			window.removeEventListener('scroll', onScroll);
			window.removeEventListener('scrollend', onScrollEnd);
			window.removeEventListener('hashchange', onHashChange);
		};
	});
</script>

<div class="space-y-4">
	<PageHeader
		title="Settings"
		description="Your account, how the app looks, and the keys, tokens and services LLM Lab connects to."
	>
		{#snippet meta()}
			{#if auth.user?.email}<span class="truncate text-xs text-muted-foreground">{auth.user.email}</span>{/if}
		{/snippet}
	</PageHeader>

	<!-- Narrow screens: sticky bookmark chips under the app bar. -->
	<nav
		class="sticky top-12 z-20 -mx-3 border-b border-border bg-background/95 backdrop-blur-md sm:-mx-4 md:-mx-5 lg:hidden"
		aria-label="Settings sections"
	>
		<ul bind:this={chipBar} class="flex gap-1.5 overflow-x-auto px-3 py-2 [scrollbar-width:none] sm:px-4 md:px-5">
			{#each SETTINGS_SECTIONS as s (s.id)}
				{@const isActive = activeId === s.id}
				<li class="shrink-0">
					<a
						href="#{s.id}"
						data-chip={s.id}
						onclick={(e) => jump(e, s.id)}
						aria-current={isActive ? 'location' : undefined}
						class={cn(
							'inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-md border px-2.5 text-sm whitespace-nowrap transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
							isActive
								? 'border-primary/40 bg-primary/10 font-medium text-primary'
								: 'border-border bg-card text-muted-foreground hover:text-foreground',
						)}
					>
						<s.icon class="size-4" aria-hidden="true" />
						{s.label}
						{#if statuses[s.id]}
							<span class={cn('status-dot size-1.5', DOT[statuses[s.id].tone])} aria-hidden="true"></span>
						{/if}
					</a>
				</li>
			{/each}
		</ul>
	</nav>

	<div class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
		<!-- Desktop: bookmark rail (same shape as the Library nav). -->
		<div class="hidden min-w-0 lg:sticky lg:top-20 lg:block lg:self-start">
			<nav
				class="space-y-4 rounded-md border border-border bg-card p-2 pt-3 shadow-[var(--shadow-card)]"
				aria-label="Settings sections"
			>
				{#each groups as group (group.id)}
					<div class="space-y-1 border-t border-border/60 pt-3 first:border-t-0 first:pt-0">
						<p class="section-label px-2">{group.label}</p>
						<ul class="space-y-0.5">
							{#each group.sections as s (s.id)}
								{@const isActive = activeId === s.id}
								{@const status = statuses[s.id]}
								<li class="relative">
									{#if isActive}<span class="absolute inset-y-1 left-0 w-0.5 rounded-full bg-primary" aria-hidden="true"></span>{/if}
									<a
										href="#{s.id}"
										onclick={(e) => jump(e, s.id)}
										aria-current={isActive ? 'location' : undefined}
										class={cn(
											'flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none',
											isActive
												? 'bg-primary/10 font-medium text-primary'
												: 'text-muted-foreground hover:bg-muted hover:text-foreground',
										)}
									>
										<s.icon class="size-4 shrink-0" aria-hidden="true" />
										<span class="flex-1 truncate">{s.label}</span>
										{#if status}
											<span class={cn('status-dot size-1.5 shrink-0', DOT[status.tone])} title={status.label} aria-hidden="true"></span>
											<span class="sr-only">status: {status.label}</span>
										{/if}
									</a>
								</li>
							{/each}
						</ul>
					</div>
				{/each}
				<button
					type="button"
					class="flex w-full cursor-pointer items-center gap-1.5 border-t border-border/60 px-2 pt-3 pb-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
					onclick={() => scrollToSection(ORDER[0])}
				>
					<ArrowUp class="size-3.5" aria-hidden="true" />Back to top
				</button>
			</nav>
		</div>

		<!-- Every section as one card, in one list. -->
		<div id="settings-body" class="min-w-0 space-y-6">
			{#each groups as group (group.id)}
				<div class="space-y-4" role="group" aria-labelledby="group-{group.id}">
					<SectionHeading id="group-{group.id}" size="sm" title={group.label} />
					{#each group.sections as s (s.id)}
						{@const Body = BODIES[s.id]}
						{@const status = statuses[s.id]}
						<section
							id={s.id}
							aria-labelledby="{s.id}-title"
							class="settings-pane group/section min-w-0 scroll-mt-28 overflow-clip rounded-md border border-border bg-card shadow-[var(--shadow-card)] lg:scroll-mt-20"
						>
							<div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border px-4 py-2.5">
								<div class="mr-auto flex min-w-0 items-center gap-1.5">
									<h3 id="{s.id}-title" tabindex="-1" class="font-display text-base font-semibold outline-none">{s.label}</h3>
									<Popover.Root>
										<Popover.Trigger class="page-header-help-trigger" aria-label="About {s.label.toLowerCase()}">
											<CircleHelp class="size-4" aria-hidden="true" />
										</Popover.Trigger>
										<Popover.Portal>
											<Popover.Content side="bottom" align="start" sideOffset={6} collisionPadding={12} class="page-header-help">
												<p>{s.description}</p>
											</Popover.Content>
										</Popover.Portal>
									</Popover.Root>
									{#if status}<StatusBadge status={status.label} tone={status.tone} class="ml-1" />{/if}
								</div>
								<button
									type="button"
									class="flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground opacity-0 transition-opacity group-hover/section:opacity-100 hover:bg-muted hover:text-foreground focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring max-lg:opacity-60"
									onclick={() => copyLink(s.id, s.label)}
									aria-label="Copy link to {s.label}"
									title="Copy link to this section"
								>
									<Link class="size-3.5" />
								</button>
							</div>
							<div class="settings-pane-body divide-y divide-border">
								<Body />
							</div>
						</section>
					{/each}
				</div>
			{/each}
		</div>
	</div>
</div>

<style>
	/* Banners and states render flush inside the section card instead of as their own surfaces. */
	.settings-pane-body > :global([role='alert']) {
		margin: 1rem;
		width: auto;
	}
	.settings-pane-body > :global(form) {
		display: block;
	}
</style>
