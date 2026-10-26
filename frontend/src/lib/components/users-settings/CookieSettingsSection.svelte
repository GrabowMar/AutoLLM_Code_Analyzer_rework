<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Separator } from '$lib/components/ui/separator';
	import { Switch } from '$lib/components/ui/switch';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import Cookie from '@lucide/svelte/icons/cookie';
	import Shield from '@lucide/svelte/icons/shield';
	import BarChart from '@lucide/svelte/icons/bar-chart-3';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import Sparkles from '@lucide/svelte/icons/sparkles';

	const prefs = getPreferences();

	interface ToggleRow {
		key: 'essential' | 'analytics' | 'functional' | 'ai';
		title: string;
		description: string;
		icon: typeof Shield;
		locked?: boolean;
	}

	const rows: ToggleRow[] = [
		{
			key: 'essential',
			title: 'Essential',
			description: 'Required for basic functionality including authentication and security.',
			icon: Shield,
			locked: true,
		},
		{
			key: 'analytics',
			title: 'Analytics & Performance',
			description: 'Help us understand how you use the application to improve performance.',
			icon: BarChart,
		},
		{
			key: 'functional',
			title: 'Functional & Preferences',
			description: 'Remember your preferences and settings for a personalized experience.',
			icon: SlidersHorizontal,
		},
		{
			key: 'ai',
			title: 'AI Processing & History',
			description: 'Store AI analysis results and processing history for faster access.',
			icon: Sparkles,
		},
	];
</script>

<Card.Root>
	<Card.Header>
		<Card.Title class="flex items-center gap-2">
			<Cookie class="h-4 w-4 text-muted-foreground" />
			Cookie &amp; Data Settings
		</Card.Title>
		<Card.Description>Manage how we use cookies and process your data.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		{#each rows as row, i (row.key)}
			{#if i > 0}<Separator />{/if}
			<div class="flex items-start justify-between gap-4">
				<div class="space-y-0.5">
					<div class="flex items-center gap-2">
						<row.icon class="h-3.5 w-3.5 text-muted-foreground" />
						<p class="text-sm font-medium">{row.title}</p>
					</div>
					<p class="text-xs text-muted-foreground">{row.description}</p>
				</div>
				{#if row.locked}
					<Switch checked={true} disabled label={row.title} />
				{:else}
					<Switch
						checked={prefs.cookieConsent[row.key]}
						label={row.title}
						onCheckedChange={(v) => prefs.setCookieConsent({ [row.key]: v })}
					/>
				{/if}
			</div>
		{/each}
	</Card.Content>
</Card.Root>
