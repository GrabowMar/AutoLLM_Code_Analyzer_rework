<script lang="ts">
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import SettingsBlock from './SettingsBlock.svelte';
	import { Switch } from '$lib/components/ui/switch';

	const prefs = getPreferences();

	const toggles = [
		{
			id: 'sidebar-collapsed',
			title: 'Collapsed sidebar',
			description: 'Show the sidebar as icons only. The toggle at the bottom of the sidebar does the same.',
			get: () => prefs.sidebarCollapsed,
			set: (v: boolean) => prefs.setSidebarCollapsed(v),
		},
		{
			id: 'compact-tables',
			title: 'Compact tables',
			description: 'Tighter rows in every data table. Table toolbars can switch density too.',
			get: () => prefs.compactTables,
			set: (v: boolean) => prefs.setCompactTables(v),
		},
	];
</script>

<SettingsBlock>
	{#each toggles as t (t.id)}
		<label for={t.id} class="settings-row flex cursor-pointer items-center justify-between gap-6 transition-colors hover:bg-muted/30">
			<span class="min-w-0 space-y-0.5">
				<span class="block text-sm font-medium">{t.title}</span>
				<span class="block text-[13px] leading-5 text-muted-foreground">{t.description}</span>
			</span>
			<Switch id={t.id} checked={t.get()} label={t.title} onCheckedChange={(v) => t.set(v)} />
		</label>
	{/each}
</SettingsBlock>
