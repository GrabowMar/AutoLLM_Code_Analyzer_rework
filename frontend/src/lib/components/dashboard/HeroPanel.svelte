<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Plus from '@lucide/svelte/icons/plus';
	import Wand2 from '@lucide/svelte/icons/wand-2';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Loader2 from '@lucide/svelte/icons/loader-2';
	import { greeting, timeAgo } from '$lib/utils/dashboard';

	interface Props {
		userName: string | null;
		isRefreshing: boolean;
		lastRefreshed: Date | null;
		onRefresh: () => void;
	}

	let { userName, isRefreshing, lastRefreshed, onRefresh }: Props = $props();
</script>

<div class="hero-panel p-4 sm:p-6">
	<div class="relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
		<div class="space-y-1.5">
			<div
				class="flex items-center gap-2 text-xs font-medium text-muted-foreground"
				style="font-family: var(--font-mono);"
			>
				<Sparkles class="h-3 w-3 text-primary" />
				<span>{greeting()}</span>
				<span class="text-primary" aria-hidden="true">/</span>
				<span>
					{new Date().toLocaleDateString(undefined, {
						weekday: 'short',
						month: 'short',
						day: 'numeric',
					})}
				</span>
			</div>
			<h1 class="display-xl cursor-blink truncate">
				{#if userName}
					<span class="text-muted-foreground">$&nbsp;</span>welcome&nbsp;<span
						class="gradient-text break-all">{userName}</span
					>
				{:else}
					<span class="text-muted-foreground">$&nbsp;</span><span class="gradient-text"
						>research_dashboard</span
					>
				{/if}
			</h1>
			<p class="text-sm text-muted-foreground" style="font-family: var(--font-mono);">
				// llm evaluation · code generation · security analysis
			</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			<Button href="/sample-generator" size="sm" class="hover-glow gap-1.5">
				<Wand2 class="h-3.5 w-3.5" />
				Generate App
			</Button>
			<Button href="/analysis/create" size="sm" variant="outline" class="gap-1.5">
				<Plus class="h-3.5 w-3.5" />
				New Analysis
			</Button>
			<Button
				onclick={onRefresh}
				size="icon"
				variant="ghost"
				class="h-9 w-9"
				disabled={isRefreshing}
				aria-label="Refresh dashboard"
			>
				{#if isRefreshing}
					<Loader2 class="h-4 w-4 animate-spin" />
				{:else}
					<RefreshCw class="h-4 w-4" />
				{/if}
			</Button>
		</div>
	</div>
	{#if lastRefreshed}
		<p
			class="relative mt-3 flex items-center gap-1.5 text-[11px] text-muted-foreground"
			style="font-family: var(--font-mono);"
		>
			<span class="status-dot status-dot-live h-1.5 w-1.5 text-primary"></span>
			live · updated {timeAgo(lastRefreshed.toISOString())}
		</p>
	{/if}
</div>
