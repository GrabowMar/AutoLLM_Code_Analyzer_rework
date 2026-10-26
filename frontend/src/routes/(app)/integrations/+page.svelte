<script lang="ts">
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { McpSection, ProvidersSection, GitHubSection, WebhooksSection } from '$lib/components/integrations';
	import Bot from '@lucide/svelte/icons/bot';
	import Cloud from '@lucide/svelte/icons/cloud';
	import GitBranch from '@lucide/svelte/icons/git-branch';
	import Bell from '@lucide/svelte/icons/bell';

	const TABS = [
		{ id: 'mcp', label: 'MCP server', icon: Bot },
		{ id: 'providers', label: 'LLM providers', icon: Cloud },
		{ id: 'github', label: 'GitHub', icon: GitBranch },
		{ id: 'notifications', label: 'Notifications', icon: Bell },
	] as const;
	type TabId = (typeof TABS)[number]['id'];

	function parseTab(value: string | null): TabId {
		return TABS.find((t) => t.id === value)?.id ?? 'mcp';
	}

	const activeTab = $derived(parseTab(page.url.searchParams.get('tab')));

	function navigate(tab: TabId) {
		const url = new URL(page.url);
		url.searchParams.set('tab', tab);
		goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}
</script>

<div class="space-y-6">
	<PageHeader
		title="Integrations"
		description="Connect assistants over MCP, add cloud model providers, export apps to GitHub and get notified when runs finish."
	/>

	<Tabs.Root value={activeTab} onValueChange={(v) => navigate(parseTab(v))}>
		<Tabs.List aria-label="Integrations">
			{#each TABS as tab (tab.id)}
				{@const Icon = tab.icon}
				<Tabs.Trigger value={tab.id} class="px-2.5 sm:px-3"><Icon class="hidden sm:block" />{tab.label}</Tabs.Trigger>
			{/each}
		</Tabs.List>
		<Tabs.Content value="mcp"><McpSection /></Tabs.Content>
		<Tabs.Content value="providers"><ProvidersSection /></Tabs.Content>
		<Tabs.Content value="github"><GitHubSection /></Tabs.Content>
		<Tabs.Content value="notifications"><WebhooksSection /></Tabs.Content>
	</Tabs.Root>
</div>
