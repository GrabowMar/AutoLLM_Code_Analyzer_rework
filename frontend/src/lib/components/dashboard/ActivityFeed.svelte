<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { fly } from 'svelte/transition';
	import { prefersReducedMotion } from 'svelte/motion';
	import Activity from '@lucide/svelte/icons/activity';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Clock from '@lucide/svelte/icons/clock';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import { timeAgo } from '$lib/utils/dashboard';
	import type { RecentActivityItem } from '$lib/api/client';

	interface Props {
		items: RecentActivityItem[];
		isRefreshing: boolean;
		onRefresh: () => void;
	}

	let { items, isRefreshing, onRefresh }: Props = $props();

	function dotColor(status: string): string {
		if (status === 'completed') return 'text-emerald-500';
		if (status === 'failed') return 'text-destructive';
		if (status === 'running') return 'text-amber-500';
		return 'text-blue-500';
	}
</script>

<Card.Root>
	<Card.Header>
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<Clock class="h-4 w-4 text-muted-foreground" />
				<Card.Title>Recent Activity</Card.Title>
			</div>
			<Button
				variant="ghost"
				size="icon"
				class="h-7 w-7"
				onclick={onRefresh}
				disabled={isRefreshing}
				aria-label="Refresh"
			>
				<RefreshCw class="h-3.5 w-3.5 {isRefreshing ? 'animate-spin' : ''}" />
			</Button>
		</div>
	</Card.Header>
	<Card.Content>
		{#if items.length > 0}
			<!-- Timeline rail -->
			<div class="relative ml-1.5 space-y-4 border-l border-border pl-4">
				{#each items as event, i (event.kind + event.id)}
					{@const EvtIcon = event.kind === 'analysis' ? Activity : AppWindow}
					<div
						class="relative"
						in:fly={{
							y: 8,
							duration: prefersReducedMotion.current ? 0 : 250,
							delay: prefersReducedMotion.current ? 0 : Math.min(i, 8) * 35,
						}}
					>
						<span
							class="absolute -left-[1.4rem] top-1 flex h-3 w-3 items-center justify-center rounded-full bg-card"
							aria-hidden="true"
						>
							<span
								class="status-dot h-2 w-2 {dotColor(event.status)} {event.status === 'running'
									? 'status-dot-live'
									: ''}"
							></span>
						</span>
						<div class="flex items-start gap-2">
							<EvtIcon class="mt-0.5 h-3.5 w-3.5 shrink-0 {dotColor(event.status)}" />
							<div class="min-w-0 flex-1">
								<p class="break-words text-xs leading-relaxed">
									<span class="capitalize">{event.kind}</span>: {event.title}
									<span class="text-muted-foreground">({event.status})</span>
								</p>
								<p class="mt-0.5 text-[11px] text-muted-foreground">{timeAgo(event.created_at)}</p>
							</div>
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="flex flex-col items-center justify-center py-8 text-center">
				<Activity class="h-8 w-8 text-muted-foreground/50" />
				<p class="mt-2 text-xs text-muted-foreground">No recent activity yet.</p>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
