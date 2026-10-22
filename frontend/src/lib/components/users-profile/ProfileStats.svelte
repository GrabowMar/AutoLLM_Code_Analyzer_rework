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
		<div class="flex flex-col gap-1 rounded-md border border-border bg-card px-4 py-3">
			<div class="flex items-center justify-between">
				<span class="kpi-label">{item.label}</span>
				<item.icon class="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
			</div>
			<p class="kpi-value">{item.value}</p>
		</div>
	{/each}
</section>
