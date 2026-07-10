<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import HeartPulse from '@lucide/svelte/icons/heart-pulse';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import type { AnalyzerHealth } from '$lib/api/client';

	interface Props {
		health: AnalyzerHealth | null;
		isRefreshing: boolean;
		onRefresh: () => void;
	}

	let { health, isRefreshing, onRefresh }: Props = $props();
</script>

<Card.Root>
	<Card.Header>
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<HeartPulse class="h-4 w-4 text-muted-foreground" />
				<div>
					<Card.Title>Analyzer Health</Card.Title>
					<Card.Description>
						{#if health}
							<span class="font-medium text-emerald-600 dark:text-emerald-400"
								>{health.online}</span
							>
							online ·
							<span class="font-medium text-destructive">{health.offline}</span> offline
						{:else}
							Loading…
						{/if}
					</Card.Description>
				</div>
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
		{#if health}
			<div class="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
				{#each health.analyzers as a (a.name)}
					<div
						class="flex flex-col gap-1.5 rounded-md border border-border bg-surface-2/50 px-3 py-2.5 transition-colors hover:border-primary/40"
					>
						<div class="flex items-center justify-between gap-2">
							<span class="truncate text-sm font-medium">{a.display_name}</span>
							<span
								class="status-dot h-2 w-2 {a.available
									? 'status-dot-live text-emerald-500'
									: 'text-destructive'}"
								role="img"
								aria-label={a.available ? 'online' : 'offline'}
							></span>
						</div>
						<div class="flex items-center justify-between gap-2">
							<span class="mono-tag uppercase">{a.type}</span>
							<span
								class="truncate text-[11px] {a.available
									? 'text-emerald-600 dark:text-emerald-400'
									: 'text-muted-foreground'}"
							>
								{a.message}
							</span>
						</div>
					</div>
				{/each}
			</div>
		{:else}
			<div class="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4" aria-hidden="true">
				{#each Array(4) as _, i (i)}
					<div class="skeleton h-[4.25rem]"></div>
				{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
