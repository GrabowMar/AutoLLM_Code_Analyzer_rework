<script lang="ts">
	import AppWindow from '@lucide/svelte/icons/app-window';
	import BarChart3 from '@lucide/svelte/icons/bar-chart-3';
	import FileText from '@lucide/svelte/icons/file-text';
	import Trophy from '@lucide/svelte/icons/trophy';

	interface Stat {
		label: string;
		value: string | number;
		icon: typeof AppWindow;
	}

	interface Props {
		stats?: Partial<Record<'applications' | 'analyses' | 'reports' | 'rank', number | null>>;
	}

	let { stats = {} }: Props = $props();

	const fmt = (v: number | null | undefined) => (v == null ? '—' : v.toLocaleString());

	let items = $derived<Stat[]>([
		{ label: 'Applications', value: fmt(stats.applications), icon: AppWindow },
		{ label: 'Analyses', value: fmt(stats.analyses), icon: BarChart3 },
		{ label: 'Reports', value: fmt(stats.reports), icon: FileText },
		{ label: 'Rank', value: stats.rank != null ? `#${stats.rank}` : '—', icon: Trophy },
	]);
</script>

<section class="grid grid-cols-2 gap-3 sm:grid-cols-4">
	{#each items as item (item.label)}
		<div class="rounded-lg border border-border bg-card p-4 transition-colors hover:border-primary/40">
			<div class="flex items-center justify-between">
				<span class="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground" style="font-family: var(--font-mono);">
					{item.label}
				</span>
				<item.icon class="h-3.5 w-3.5 text-muted-foreground/70" />
			</div>
			<p class="mt-2 text-2xl font-semibold tabular-nums tracking-tight" style="font-family: var(--font-display);">
				{item.value}
			</p>
		</div>
	{/each}
</section>
