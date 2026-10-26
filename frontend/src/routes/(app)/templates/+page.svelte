<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { formatApiError } from '$lib/api/core';
	import type { PresetKind } from '$lib/api/generation';
	import { LibraryData, type ConfirmRequest } from '$lib/components/templates/library.svelte';
	import LibraryNav, { type LibraryNavGroup, type LibraryTab } from '$lib/components/templates/LibraryNav.svelte';
	import SpecsSection from '$lib/components/templates/SpecsSection.svelte';
	import StacksSection from '$lib/components/templates/StacksSection.svelte';
	import PresetsSection from '$lib/components/templates/PresetsSection.svelte';
	import ProfilesSection from '$lib/components/templates/ProfilesSection.svelte';
	import BlocksSection from '$lib/components/templates/BlocksSection.svelte';
	import PackagesDialog from '$lib/components/templates/PackagesDialog.svelte';
	import { isPresetKind } from '$lib/components/templates/presets';
	import Bot from '@lucide/svelte/icons/bot';
	import FileText from '@lucide/svelte/icons/file-text';
	import Gauge from '@lucide/svelte/icons/gauge';
	import Layers from '@lucide/svelte/icons/layers';
	import Package from '@lucide/svelte/icons/package';
	import Puzzle from '@lucide/svelte/icons/puzzle';
	import ScrollText from '@lucide/svelte/icons/scroll-text';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

	// ── URL state: ?tab=specs|stacks|presets|profiles|blocks&kind=harness|sampling|budget ──
	const TABS: LibraryTab[] = ['specs', 'stacks', 'presets', 'profiles', 'blocks'];

	function parseTab(raw: string | null): LibraryTab {
		if (raw === 'app') return 'specs'; // pre-rework links
		return TABS.includes(raw as LibraryTab) ? (raw as LibraryTab) : 'specs';
	}

	const activeTab = $derived(parseTab(page.url.searchParams.get('tab')));
	const presetKind = $derived.by((): PresetKind => {
		const raw = page.url.searchParams.get('kind');
		return isPresetKind(raw) ? raw : 'harness';
	});

	const data = new LibraryData();
	onMount(() => data.init());

	const count = (r: { loading: boolean; items: unknown[] }) => (r.loading ? null : r.items.length);
	const presetCount = (k: PresetKind) => (data.presets.loading ? null : data.presetCounts[k]);

	const groups = $derived<LibraryNavGroup[]>([
		{ title: 'What to build', items: [{ tab: 'specs', label: 'App specs', icon: FileText, count: count(data.specs) }] },
		{ title: 'Where it runs', items: [{ tab: 'stacks', label: 'Stacks', icon: Layers, count: count(data.stacks) }] },
		{
			title: 'How agents run',
			items: [
				{ tab: 'presets', kind: 'harness', label: 'Harness', icon: Bot, count: presetCount('harness') },
				{ tab: 'presets', kind: 'sampling', label: 'Sampling', icon: SlidersHorizontal, count: presetCount('sampling') },
				{ tab: 'presets', kind: 'budget', label: 'Budget', icon: Gauge, count: presetCount('budget') },
			],
		},
		{
			title: 'Template prompts',
			note: 'Template scaffolding mode only',
			items: [
				{ tab: 'profiles', label: 'Profiles', icon: ScrollText, count: count(data.profiles) },
				{ tab: 'blocks', label: 'Blocks', icon: Puzzle, count: count(data.blocks) },
			],
		},
	]);

	let packagesOpen = $state(false);

	// Confirmation for destructive actions, shared by every section.
	let confirmState = $state<ConfirmRequest | null>(null);
	let confirmOpen = $state(false);
	let confirmBusy = $state(false);
	let actionError = $state('');

	function askConfirm(request: ConfirmRequest) {
		actionError = '';
		confirmState = request;
		confirmOpen = true;
	}

	async function runConfirmed() {
		if (!confirmState) return;
		confirmBusy = true;
		try {
			await confirmState.action();
		} catch (e) {
			actionError = formatApiError(e, 'The action failed.');
		} finally {
			confirmBusy = false;
			confirmOpen = false;
		}
	}
</script>

<div class="space-y-4">
	<PageHeader
		title="Generation Library"
		description="Everything a sample run is assembled from: what to build, the stack it runs on, how the agent runs, and the prompts template mode uses."
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" onclick={() => (packagesOpen = true)}>
				<Package /> Packages
			</Button>
		{/snippet}
	</PageHeader>

	{#if actionError}
		<ErrorAlert message={actionError} />
	{/if}

	<div class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[13rem_minmax(0,1fr)]">
		<div class="min-w-0 lg:sticky lg:top-20 lg:self-start">
			<LibraryNav {groups} {activeTab} activeKind={presetKind} onNavigate={() => (actionError = '')} />
		</div>

		<div class="min-w-0">
			{#if activeTab === 'specs'}
				<SpecsSection {data} {askConfirm} />
			{:else if activeTab === 'stacks'}
				<StacksSection {data} {askConfirm} />
			{:else if activeTab === 'presets'}
				<PresetsSection {data} kind={presetKind} {askConfirm} />
			{:else if activeTab === 'profiles'}
				<ProfilesSection {data} {askConfirm} />
			{:else}
				<BlocksSection {data} />
			{/if}
		</div>
	</div>
</div>

<PackagesDialog
	bind:open={packagesOpen}
	profiles={data.profiles.items}
	appTemplates={data.specs.items}
	contentBlocks={data.blocks.items}
	stacks={data.stacks.items}
	presets={data.presets.items}
	starterPackages={data.starters.items}
	starterPackagesLoading={data.starters.loading}
	starterPackagesError={data.starters.error}
	onRetryStarters={() => data.starters.load()}
	onImported={() => data.refreshAll()}
/>

<ConfirmDialog
	bind:open={confirmOpen}
	title={confirmState?.title ?? ''}
	description={confirmState?.description ?? ''}
	confirmLabel={confirmState?.confirmLabel ?? 'Confirm'}
	destructive
	busy={confirmBusy}
	onConfirm={runConfirmed}
/>
