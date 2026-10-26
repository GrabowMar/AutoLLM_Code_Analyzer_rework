<script lang="ts">
	import { onMount, type Component, type Snippet } from 'svelte';
	import { DropdownMenu } from 'bits-ui';
	import { goto } from '$app/navigation';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Plus from '@lucide/svelte/icons/plus';
	import Wand2 from '@lucide/svelte/icons/wand-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Loader2 from '@lucide/svelte/icons/loader-2';
	import Wrench from '@lucide/svelte/icons/wrench';
	import CloudDownload from '@lucide/svelte/icons/cloud-download';
	import Eraser from '@lucide/svelte/icons/eraser';
	import Activity from '@lucide/svelte/icons/activity';
	import Cpu from '@lucide/svelte/icons/cpu';
	import { timeAgo } from '$lib/utils/dashboard';
	import { getMe } from '$lib/api/users';

	interface Props {
		isRefreshing: boolean;
		lastRefreshed: Date | null;
		onRefresh: () => void;
		/** Running maintenance action ('sync' | 'caches' | 'stuck'), or null. */
		pendingAction?: string | null;
		onSyncModels?: () => void;
		onClearCaches?: () => void;
		onClearStuck?: () => void;
		/** Inline counts for the header's footer strip. */
		stats?: Snippet;
	}

	let {
		isRefreshing,
		lastRefreshed,
		onRefresh,
		pendingAction = null,
		onSyncModels,
		onClearCaches,
		onClearStuck,
		stats,
	}: Props = $props();

	// Every maintenance action is staff-only on the API, so non-staff never see the menu
	// (same check as the sidebar's staffOnly sections).
	let isStaff = $state(false);
	onMount(async () => {
		try {
			isStaff = (await getMe()).is_staff ?? false;
		} catch {
			isStaff = false;
		}
	});

	const itemClass =
		'flex cursor-pointer select-none items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors duration-150 data-[highlighted]:bg-surface-2 data-[disabled]:pointer-events-none data-[disabled]:opacity-50 [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:text-muted-foreground';
</script>

{#snippet actionIcon(key: string, Icon: Component)}
	{#if pendingAction === key}<Loader2 class="animate-spin motion-reduce:animate-none" />{:else}<Icon />{/if}
{/snippet}

<!-- Plain page header: the dashboard is an instrument panel, not a landing page. -->
<PageHeader title="Dashboard" description="Generation, analysis and evaluation activity across the lab." children={stats}>
	{#snippet meta()}
		{#if lastRefreshed}
			<span class="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
				<span class="status-dot status-dot-live h-1.5 w-1.5 text-success"></span>
				live · {timeAgo(lastRefreshed.toISOString())}
			</span>
		{/if}
	{/snippet}
	{#snippet actions()}
		<Button onclick={onRefresh} size="sm" variant="outline" disabled={isRefreshing} aria-label="Refresh dashboard" title="Refresh dashboard">
			{#if isRefreshing}
				<Loader2 class="h-3.5 w-3.5 animate-spin" />
			{:else}
				<RefreshCw class="h-3.5 w-3.5" />
			{/if}
		</Button>
		<!-- Maintenance shortcuts (formerly the Quick Actions card); staff only. -->
		{#if isStaff}
		<DropdownMenu.Root>
			<DropdownMenu.Trigger>
				{#snippet child({ props })}
					<Button {...props} size="sm" variant="outline" aria-label="Maintenance actions" title="Maintenance">
						{#if pendingAction}<Loader2 class="h-3.5 w-3.5 animate-spin" />{:else}<Wrench class="h-3.5 w-3.5" />{/if}
					</Button>
				{/snippet}
			</DropdownMenu.Trigger>
			<DropdownMenu.Portal>
				<DropdownMenu.Content
					align="end"
					sideOffset={6}
					collisionPadding={12}
					class="z-50 min-w-48 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
				>
					<DropdownMenu.Item class={itemClass} disabled={pendingAction !== null} onSelect={() => onSyncModels?.()}>
						{@render actionIcon('sync', CloudDownload)}Sync models
					</DropdownMenu.Item>
					<DropdownMenu.Item class={itemClass} disabled={pendingAction !== null} onSelect={() => onClearCaches?.()}>
						{@render actionIcon('caches', Eraser)}Clear caches
					</DropdownMenu.Item>
					<DropdownMenu.Item class={itemClass} disabled={pendingAction !== null} onSelect={() => onClearStuck?.()}>
						{@render actionIcon('stuck', Activity)}Clear stuck jobs
					</DropdownMenu.Item>
					<DropdownMenu.Separator class="-mx-1 my-1 h-px bg-border" />
					<DropdownMenu.Item class={itemClass} onSelect={() => goto('/system')}>
						<Cpu />Open system panel
					</DropdownMenu.Item>
				</DropdownMenu.Content>
			</DropdownMenu.Portal>
		</DropdownMenu.Root>
		{/if}
		<Button href="/analysis/create" size="sm" variant="outline" class="gap-1.5">
			<Plus class="h-3.5 w-3.5" />
			New analysis
		</Button>
		<Button href="/sample-generator" size="sm" class="gap-1.5">
			<Wand2 class="h-3.5 w-3.5" />
			Generate app
		</Button>
	{/snippet}
</PageHeader>
