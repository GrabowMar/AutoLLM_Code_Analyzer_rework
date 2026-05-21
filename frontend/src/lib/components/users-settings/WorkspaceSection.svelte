<script lang="ts">
	import { getPreferences, VALID_ITEMS_PER_PAGE } from '$lib/stores/preferences.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Separator } from '$lib/components/ui/separator';
	import { Switch } from '$lib/components/ui/switch';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';

	const prefs = getPreferences();

	interface ToggleRow {
		id: string;
		title: string;
		description: string;
		get: () => boolean;
		set: (v: boolean) => void;
	}

	const toggles: ToggleRow[] = [
		{
			id: 'sidebar-collapsed',
			title: 'Start sidebar collapsed',
			description: 'Sidebar opens in its collapsed icon-only state on every page load.',
			get: () => prefs.sidebarCollapsed,
			set: (v) => prefs.setSidebarCollapsed(v),
		},
		{
			id: 'compact-tables',
			title: 'Compact tables',
			description: 'Reduce padding in table rows for a denser layout.',
			get: () => prefs.compactTables,
			set: (v) => prefs.setCompactTables(v),
		},
		{
			id: 'advanced-options',
			title: 'Show advanced options',
			description: 'Display extra configuration controls intended for power users.',
			get: () => prefs.showAdvancedOptions,
			set: (v) => prefs.setShowAdvancedOptions(v),
		},
		{
			id: 'auto-refresh',
			title: 'Auto-refresh dashboards',
			description: 'Automatically refresh dashboard data at a regular interval.',
			get: () => prefs.autoRefresh,
			set: (v) => prefs.setAutoRefresh(v),
		},
	];
</script>

<Card.Root>
	<Card.Header>
		<Card.Title class="flex items-center gap-2">
			<LayoutDashboard class="h-4 w-4 text-muted-foreground" />
			Workspace
		</Card.Title>
		<Card.Description>Layout density and data display defaults across the app.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-5">
		<div class="grid gap-4 sm:grid-cols-2">
			<label class="flex flex-col gap-1.5">
				<span class="text-[11px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">Items per page</span>
				<select
					class="h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					value={prefs.itemsPerPage}
					onchange={(e) => prefs.setItemsPerPage(Number(e.currentTarget.value) as 10 | 25 | 50 | 100)}
				>
					{#each VALID_ITEMS_PER_PAGE as count}
						<option value={count}>{count}</option>
					{/each}
				</select>
				<span class="text-xs text-muted-foreground">Rows shown per page in lists and tables.</span>
			</label>

			<label class="flex flex-col gap-1.5">
				<span class="text-[11px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">Date format</span>
				<select
					class="h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					value={prefs.dateFormat}
					onchange={(e) => prefs.setDateFormat(e.currentTarget.value as 'relative' | 'absolute' | 'iso')}
				>
					<option value="relative">Relative ("2h ago")</option>
					<option value="absolute">Absolute ("Mar 12, 14:03")</option>
					<option value="iso">ISO 8601</option>
				</select>
				<span class="text-xs text-muted-foreground">How timestamps render throughout the UI.</span>
			</label>
		</div>

		<Separator />

		<div class="space-y-4">
			{#each toggles as t, i (t.id)}
				{#if i > 0}<Separator class="opacity-60" />{/if}
				<div class="flex items-start justify-between gap-4">
					<div class="space-y-0.5">
						<p class="text-sm font-medium">{t.title}</p>
						<p class="text-xs text-muted-foreground">{t.description}</p>
					</div>
					<Switch
						id={t.id}
						checked={t.get()}
						label={t.title}
						onCheckedChange={(v) => t.set(v)}
					/>
				</div>
			{/each}
		</div>
	</Card.Content>
</Card.Root>
