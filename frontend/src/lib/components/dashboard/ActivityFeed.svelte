<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import Activity from '@lucide/svelte/icons/activity';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Clock from '@lucide/svelte/icons/clock';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { timeAgo } from '$lib/utils/dashboard';
	import type { RecentActivityItem } from '$lib/api/client';

	interface Props {
		items: RecentActivityItem[];
		isRefreshing: boolean;
		onRefresh: () => void;
	}

	let { items, isRefreshing, onRefresh }: Props = $props();

	function dotColor(status: string): string {
		if (status === 'completed') return 'text-success';
		if (status === 'failed') return 'text-destructive';
		if (status === 'running') return 'text-info';
		return 'text-muted-foreground';
	}

	// Generation titles are "Generation run (App (agentic)) — <model>": lead with the model (the part
	// that differs row to row) and demote the run kind; analysis titles are the run's own name.
	function parts(event: RecentActivityItem): { main: string; sub: string } {
		const t = event.title.replace(/^(Generation|Analysis)\s+/i, '');
		const m = t.match(/^run \((.*)\) — (.+)$/);
		if (m) return { main: m[2], sub: m[1] };
		return { main: t, sub: event.kind === 'analysis' ? 'analysis' : '' };
	}
	const hrefFor = (e: RecentActivityItem) => (e.kind === 'analysis' ? `/analysis/${e.id}` : `/samples/${e.id}`);
</script>

<Card.Root class="h-full">
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
			<ul class="-mx-1 text-xs">
				{#each items as event (event.kind + event.id)}
					{@const EvtIcon = event.kind === 'analysis' ? Activity : AppWindow}
					{@const p = parts(event)}
					<li>
					<a
						href={hrefFor(event)}
						class="flex min-h-7 items-center gap-2 rounded px-1 py-1 transition-colors duration-150 hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-ring"
						title="{event.title} ({event.status})"
					>
						<span
							class="status-dot h-1.5 w-1.5 {dotColor(event.status)} {event.status === 'running' ? 'status-dot-live' : ''}"
							role="img"
							aria-label={event.status}
						></span>
						<EvtIcon class="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-label={event.kind} />
						<span class="min-w-0 flex-1 truncate"
							><span class="text-foreground">{p.main}</span>{#if p.sub}<span class="text-muted-foreground">{` · ${p.sub}`}</span>{/if}</span
						>
						<span class="shrink-0 font-mono text-[11px] text-muted-foreground tabular-nums">{timeAgo(event.created_at)}</span>
					</a>
					</li>
				{/each}
			</ul>
		{:else}
			<EmptyState compact icon={Activity} title="No recent activity yet" />
		{/if}
	</Card.Content>
</Card.Root>
