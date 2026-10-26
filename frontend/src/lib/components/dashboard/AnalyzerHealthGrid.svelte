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
							<span class="font-medium text-success-fg"
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
			<!-- One line per analyzer: a status dot, the name and its type. -->
			<ul class="grid grid-cols-2 gap-x-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6">
				{#each health.analyzers as a (a.name)}
					<li
						class="flex min-w-0 items-center gap-2 border-b border-border/50 py-1 text-xs"
						title="{a.display_name} · {a.type} · {a.message}"
					>
						<span
							class="status-dot h-1.5 w-1.5 shrink-0 {a.available ? 'text-success' : 'text-destructive'}"
							role="img"
							aria-label={a.available ? 'online' : 'offline'}
						></span>
						<span class="min-w-0 flex-1 truncate font-medium">{a.display_name}</span>
						<span class="shrink-0 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">{a.type}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<div class="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6" aria-hidden="true">
				{#each Array(6) as _, i (i)}
					<div class="skeleton h-5"></div>
				{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
