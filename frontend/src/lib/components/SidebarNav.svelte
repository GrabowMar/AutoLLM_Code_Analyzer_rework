<script lang="ts">
	import { page } from '$app/state';
	import { onMount } from 'svelte';
	import { cn } from '$lib/utils';
	import { getMe } from '$lib/api/client';
	import { navSections, isNavActive } from '$lib/nav';

	interface Props {
		collapsed?: boolean;
		onnavigate?: () => void;
	}

	let { collapsed = false, onnavigate }: Props = $props();

	let isStaff = $state(false);

	onMount(async () => {
		try {
			isStaff = (await getMe()).is_staff ?? false;
		} catch {
			isStaff = false;
		}
	});

	const sections = $derived(navSections.filter((s) => !s.staffOnly || isStaff));
</script>

<nav aria-label="Main" class="flex-1 min-h-0 overflow-y-auto no-scrollbar py-1.5">
	{#each sections as section, si (section.title)}
		{#if collapsed}
			<div class="mx-2.5 my-2 h-px bg-sidebar-foreground/10" aria-hidden="true"></div>
		{:else}
			<div class="flex items-center gap-2 px-3 pt-4 pb-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.14em] text-sidebar-foreground/55">
				<span class="tabular-nums text-sidebar-foreground/35">{String(si + 1).padStart(2, '0')}</span>
				<span>{section.title}</span>
				<span class="h-px flex-1 bg-sidebar-border" aria-hidden="true"></span>
			</div>
		{/if}

		<ul class={cn('flex flex-col gap-px', collapsed ? 'px-1.5' : 'px-2')}>
			{#each section.items as item (item.href)}
				{@const active = isNavActive(page.url.pathname, item.href)}
				<li>
					<a
						href={item.href}
						aria-current={active ? 'page' : undefined}
						onclick={onnavigate}
						title={collapsed ? item.label : undefined}
						class={cn(
							'group relative flex items-center rounded text-[13px] transition-colors duration-150 motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring',
							collapsed ? 'justify-center py-2' : 'gap-2.5 px-2.5 py-1.5',
							active
								? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
								: 'text-sidebar-foreground/75 hover:bg-sidebar-accent/60 hover:text-sidebar-foreground'
						)}
					>
						{#if active}
							<span class={cn('absolute top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-sidebar-primary', collapsed ? '-left-1.5' : '-left-2')} aria-hidden="true"></span>
						{/if}
						<item.icon class={cn('h-4 w-4 shrink-0', active ? 'text-sidebar-primary' : 'opacity-80')} />
						{#if collapsed}
							<span class="sr-only">{item.label}</span>
							<span
								class="pointer-events-none absolute left-full z-50 ml-2 hidden whitespace-nowrap rounded border border-border bg-popover px-2 py-1 text-xs font-medium text-popover-foreground shadow-lg group-hover:block group-focus-visible:block"
								aria-hidden="true"
							>
								{item.label}
							</span>
						{:else}
							{item.label}
						{/if}
					</a>
				</li>
			{/each}
		</ul>
	{/each}
</nav>
