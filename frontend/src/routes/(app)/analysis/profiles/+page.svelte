<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { formatApiError } from '$lib/api/core';
	import {
		archiveAnalysisProfile,
		unarchiveAnalysisProfile,
		getAnalysisProfile,
		listAnalysisProfiles,
		type AnalysisProfile,
		type ProfileVersion
	} from '$lib/api/analysis';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ProfileCloneDialog from '$lib/components/analysis/profile/ProfileCloneDialog.svelte';
	import ProfileVersionList from '$lib/components/analysis/profile/ProfileVersionList.svelte';
	import {
		requiredCount,
		reviewerSummary,
		shortHash
	} from '$lib/components/analysis/profile/profile-utils';
	import Archive from '@lucide/svelte/icons/archive';
	import ArchiveRestore from '@lucide/svelte/icons/archive-restore';
	import Bot from '@lucide/svelte/icons/bot';
	import Copy from '@lucide/svelte/icons/copy';
	import History from '@lucide/svelte/icons/history';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import Search from '@lucide/svelte/icons/search';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

	let profiles = $state<AnalysisProfile[]>([]);
	let loading = $state(true);
	let error = $state('');
	let search = $state('');
	let showArchived = $state(false);
	let onlyMine = $state(false);

	async function load() {
		error = '';
		try {
			profiles = await listAnalysisProfiles({
				include_archived: showArchived,
				mine: onlyMine || undefined
			});
		} catch (e) {
			error = formatApiError(e, 'Could not load the analysis profiles.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	function toggleArchived(on: boolean) {
		showArchived = on;
		void load();
	}

	function toggleMine(on: boolean) {
		onlyMine = on;
		void load();
	}

	let unarchiving = $state<string | null>(null);
	async function unarchive(profile: AnalysisProfile) {
		unarchiving = profile.id;
		try {
			await unarchiveAnalysisProfile(profile.id);
			toast.success(`Restored ${profile.name}: it can be picked for new runs and studies again`);
			await load();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not restore the profile.'));
		} finally {
			unarchiving = null;
		}
	}

	const query = $derived(search.trim().toLowerCase());
	const filtered = $derived(
		profiles.filter(
			(p) =>
				!query ||
				p.name.toLowerCase().includes(query) ||
				p.slug.toLowerCase().includes(query) ||
				p.description.toLowerCase().includes(query) ||
				p.summary.tools.some((t) => t.includes(query)) ||
				p.summary.reviewer.model.toLowerCase().includes(query)
		)
	);
	const mine = $derived(filtered.filter((p) => !p.is_system && p.is_owner));
	const system = $derived(filtered.filter((p) => p.is_system));
	const others = $derived(filtered.filter((p) => !p.is_system && !p.is_owner));

	// ── version history (lazy, per row) ───────────────────────────────
	let historyId = $state<string | null>(null);
	let historyLoading = $state(false);
	let historyError = $state('');
	let historyVersions = $state<ProfileVersion[]>([]);

	async function toggleHistory(profile: AnalysisProfile) {
		if (historyId === profile.id) {
			historyId = null;
			return;
		}
		historyId = profile.id;
		historyLoading = true;
		historyError = '';
		try {
			const detail = await getAnalysisProfile(profile.id);
			if (historyId === profile.id) historyVersions = detail.versions;
		} catch (e) {
			if (historyId === profile.id) {
				historyVersions = [];
				historyError = formatApiError(e, 'Could not load the versions.');
			}
		} finally {
			if (historyId === profile.id) historyLoading = false;
		}
	}

	// ── clone / archive ────────────────────────────────────────────────
	let cloneOpen = $state(false);
	let cloneSource = $state<AnalysisProfile | null>(null);

	function startClone(profile: AnalysisProfile) {
		cloneSource = profile;
		cloneOpen = true;
	}

	function onCloned(created: AnalysisProfile) {
		toast.success(`Created ${created.name}`);
		void goto(`/analysis/profiles/${created.id}`);
	}

	let archiveTarget = $state<AnalysisProfile | null>(null);
	let archiveOpen = $state(false);
	let archiving = $state(false);

	function startArchive(profile: AnalysisProfile) {
		archiveTarget = profile;
		archiveOpen = true;
	}

	async function confirmArchive() {
		if (!archiveTarget) return;
		archiving = true;
		try {
			const result = await archiveAnalysisProfile(archiveTarget.id);
			toast.success(
				`Archived ${archiveTarget.name} (${result.versions} version${result.versions === 1 ? '' : 's'})`
			);
			archiveOpen = false;
			await load();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not archive the profile.'));
		} finally {
			archiving = false;
		}
	}
</script>

{#snippet profileRow(profile: AnalysisProfile, index: number)}
	{@const editable = profile.is_owner && !profile.is_system && !profile.is_archived}
	{@const required = requiredCount(profile.config)}
	<li
		class={cn(
			'rise-in rounded-md border border-border bg-card',
			historyId === profile.id && 'ring-1 ring-primary/40'
		)}
		style="--stagger-i: {Math.min(index, 8)}"
	>
		<div class="flex flex-col gap-3 p-4 sm:flex-row sm:items-start">
			<div class="min-w-0 flex-1 space-y-1.5">
				<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
					<a
						href="/analysis/profiles/{profile.id}"
						class="rounded text-sm font-semibold underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
					>
						{profile.name}
					</a>
					<span class="font-mono text-[11px] text-muted-foreground"
						>{profile.slug}@v{profile.version}</span
					>
					{#if profile.is_system}
						<Badge variant="secondary">System</Badge>
					{:else if profile.is_owner}
						<Badge variant="outline">Yours</Badge>
					{:else}
						<Badge variant="outline">Other user</Badge>
					{/if}
					{#if profile.is_archived}<Badge variant="secondary">Archived</Badge>{/if}
				</div>
				{#if profile.description}
					<p class="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
						{profile.description}
					</p>
				{/if}
				<div class="flex flex-wrap items-center gap-1.5 pt-0.5 text-[11px]">
					<span
						class="inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 font-medium tabular-nums"
						title={profile.summary.tools.join(', ') || 'No static tools'}
					>
						<ScanSearch class="size-3 text-muted-foreground" aria-hidden="true" />
						{profile.summary.tool_count} tool{profile.summary.tool_count === 1 ? '' : 's'}
						<span class="font-normal text-muted-foreground">· {required} required</span>
					</span>
					<span
						class={cn(
							'inline-flex items-center gap-1 rounded-full border px-2 py-0.5',
							profile.summary.reviewer.enabled
								? 'border-border font-medium'
								: 'border-dashed border-border text-muted-foreground'
						)}
						title={profile.summary.reviewer.enabled
							? profile.summary.reviewer.model
							: 'Static measurements only'}
					>
						<Bot class="size-3 text-muted-foreground" aria-hidden="true" />
						{reviewerSummary(profile.summary.reviewer)}
					</span>
					<span class="font-mono text-muted-foreground" title="Content hash {profile.content_hash}"
						>#{shortHash(profile.content_hash, 8)}</span
					>
					<span class="text-muted-foreground">· updated {formatDate(profile.updated_at)}</span>
				</div>
			</div>
			<div class="flex shrink-0 flex-wrap gap-1.5 sm:self-center">
				<Button
					href="/analysis/profiles/{profile.id}"
					variant="outline"
					size="sm"
					aria-label="{editable ? 'Edit' : 'View'} {profile.name}"
				>
					{#if editable}<Pencil />Edit{:else}View{/if}
				</Button>
				<Button
					variant="outline"
					size="sm"
					title="Copy into a new profile of yours"
					aria-label="{profile.is_system ? 'Customize' : 'Clone'} {profile.name}"
					onclick={() => startClone(profile)}
				>
					<Copy />{#if profile.is_system}<span>Customize</span>{/if}
				</Button>
				<Button
					variant="outline"
					size="sm"
					title="Version history"
					aria-label="Version history of {profile.name}"
					aria-expanded={historyId === profile.id}
					onclick={() => toggleHistory(profile)}
				>
					<History />
				</Button>
				{#if profile.is_archived && profile.is_owner && !profile.is_system}
					<Button
						variant="outline"
						size="sm"
						title="Restore every version"
						aria-label="Restore {profile.name}"
						disabled={unarchiving === profile.id}
						onclick={() => unarchive(profile)}
					>
						<ArchiveRestore />Restore
					</Button>
				{/if}
				{#if editable}
					<Button
						variant="outline"
						size="sm"
						class="hover:bg-destructive/10 hover:text-danger-fg"
						title="Archive every version (runs and studies keep theirs)"
						aria-label="Archive {profile.name}"
						onclick={() => startArchive(profile)}
					>
						<Archive />
					</Button>
				{/if}
			</div>
		</div>
		{#if historyId === profile.id}
			<div class="border-t border-border bg-muted/20 px-4 py-3">
				{#if historyLoading}
					<LoadingState label="loading versions" class="py-3" />
				{:else if historyError}
					<p class="text-xs text-danger-fg" role="alert">{historyError}</p>
				{:else}
					<ProfileVersionList
						versions={historyVersions}
						currentId={profile.id}
						latestVersion={profile.latest_version}
						label="Versions of {profile.name}"
						class="grid gap-1 sm:grid-cols-2 lg:grid-cols-3"
					/>
				{/if}
			</div>
		{/if}
	</li>
{/snippet}

{#snippet group(title: string, list: AnalysisProfile[], hint: string)}
	<section class="space-y-2" aria-label={title}>
		<div class="flex flex-wrap items-baseline gap-x-2">
			<h2 class="section-label">{title}</h2>
			<span class="text-[11px] tabular-nums text-muted-foreground">{list.length}</span>
			<span class="text-[11px] text-muted-foreground">· {hint}</span>
		</div>
		<ul class="space-y-2">
			{#each list as profile, i (profile.id)}
				{@render profileRow(profile, i)}
			{/each}
		</ul>
	</section>
{/snippet}

<div class="space-y-6">
	<PageHeader
		title="Analysis profiles"
		description="Versioned tool, AI reviewer, scope and outcome settings. Runs and studies pin one version, so editing a profile never changes past results."
	>
		{#snippet actions()}
			<Button href="/analysis/tools" variant="outline" size="sm"><ScanSearch />Tools</Button>
			<Button href="/analysis/profiles/new" size="sm"><Plus />New profile</Button>
		{/snippet}
	</PageHeader>

	<div class="flex flex-wrap items-center gap-x-4 gap-y-2">
		<div class="relative w-full sm:w-72">
			<Search
				class="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground"
				aria-hidden="true"
			/>
			<Input
				bind:value={search}
				type="search"
				placeholder="Search name, tool or model…"
				aria-label="Search profiles"
				class="h-9 pl-8"
			/>
		</div>
		<label class="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
			<Checkbox checked={showArchived} onCheckedChange={(on) => toggleArchived(on === true)} />
			Show archived
		</label>
		<label class="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
			<Checkbox checked={onlyMine} onCheckedChange={(on) => toggleMine(on === true)} />
			Only mine
		</label>
		{#if !loading && !error}
			<span class="text-xs tabular-nums text-muted-foreground sm:ml-auto" aria-live="polite">
				{filtered.length} of {profiles.length} profile{profiles.length === 1 ? '' : 's'}
			</span>
		{/if}
	</div>

	{#if error}<ErrorAlert message={error} onretry={load} />{/if}

	{#if loading}
		<LoadingState label="loading profiles" />
	{:else if !error}
		{#if profiles.length === 0}
			<EmptyState
				icon={SlidersHorizontal}
				title="No profiles available"
				description="System profiles are seeded with the analysis app; create one of your own to get started."
			>
				{#snippet action()}
					<Button href="/analysis/profiles/new" size="sm"><Plus />New profile</Button>
				{/snippet}
			</EmptyState>
		{:else if filtered.length === 0}
			<EmptyState
				compact
				icon={Search}
				title="No profiles match"
				description="Clear the search to see every profile."
			>
				{#snippet action()}
					<Button variant="outline" size="sm" onclick={() => (search = '')}>Clear search</Button>
				{/snippet}
			</EmptyState>
		{:else}
			{#if mine.length > 0}
				{@render group('Your profiles', mine, 'editable; a config change saves a new version')}
			{:else if !query}
				<section class="space-y-2" aria-label="Your profiles">
					<h2 class="section-label">Your profiles</h2>
					<EmptyState
						compact
						class="rounded-md border border-dashed border-border"
						icon={SlidersHorizontal}
						title="No profiles of yours yet"
						description="Customize a system profile below, or start a new one from the research default."
					>
						{#snippet action()}
							<Button href="/analysis/profiles/new" variant="outline" size="sm"
								><Plus />New profile</Button
							>
						{/snippet}
					</EmptyState>
				</section>
			{/if}
			{#if system.length > 0}
				{@render group('System profiles', system, 'read-only; customize to change')}
			{/if}
			{#if others.length > 0}
				{@render group('Other users', others, 'visible to staff')}
			{/if}
		{/if}
	{/if}
</div>

<ProfileCloneDialog bind:open={cloneOpen} profile={cloneSource} oncloned={onCloned} />

<ConfirmDialog
	bind:open={archiveOpen}
	title="Archive {archiveTarget?.name ?? 'profile'}?"
	description="Every version of {archiveTarget?.slug ??
		'this profile'} is archived and can no longer be picked for new runs or studies. Runs and studies that used it keep their pinned version."
	confirmLabel="Archive"
	destructive
	busy={archiving}
	onConfirm={confirmArchive}
/>
