<script lang="ts">
	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import {
		archiveAnalysisProfile,
		unarchiveAnalysisProfile,
		getAnalysisProfile,
		updateAnalysisProfile,
		type AnalysisProfile,
		type AnalysisProfileDetail,
		type ProfileConfig,
		type ProfileDefaults
	} from '$lib/api/analysis';
	import { formatDate } from '$lib/utils/formatters';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import ProfileEditor, {
		type ProfileEditorSection,
		type ProfileValidationState
	} from '$lib/components/analysis/profile/ProfileEditor.svelte';
	import ProfileCloneDialog from '$lib/components/analysis/profile/ProfileCloneDialog.svelte';
	import ProfileVersionList from '$lib/components/analysis/profile/ProfileVersionList.svelte';
	import { loadProfileDefaults } from '$lib/components/analysis/profile/defaults';
	import {
		cloneJson,
		shortHash,
		stableStringify
	} from '$lib/components/analysis/profile/profile-utils';
	import Archive from '@lucide/svelte/icons/archive';
	import Copy from '@lucide/svelte/icons/copy';
	import History from '@lucide/svelte/icons/history';
	import Info from '@lucide/svelte/icons/info';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Lock from '@lucide/svelte/icons/lock';
	import Save from '@lucide/svelte/icons/save';
	import Undo2 from '@lucide/svelte/icons/undo-2';

	const SECTIONS: ProfileEditorSection[] = ['tools', 'reviewer', 'scope', 'outcomes'];

	const profileId = $derived(page.params.id ?? '');

	let profile = $state<AnalysisProfileDetail | null>(null);
	let defaults = $state<ProfileDefaults | null>(null);
	let loading = $state(true);
	let loadError = $state('');

	let draft = $state<ProfileConfig | null>(null);
	let name = $state('');
	let description = $state('');
	let validation = $state<ProfileValidationState>({
		status: 'idle',
		errors: {},
		contentHash: null,
		config: null,
		message: ''
	});
	let invalid = $state(false);
	let saving = $state(false);
	let saveError = $state('');
	let fieldErrors = $state<Record<string, string>>({});

	function reset(detail: AnalysisProfileDetail) {
		profile = detail;
		draft = cloneJson(detail.config);
		name = detail.name;
		description = detail.description;
		fieldErrors = {};
		saveError = '';
	}

	async function load(id: string, signal?: AbortSignal) {
		loading = true;
		loadError = '';
		try {
			const [detail, catalog] = await Promise.all([
				getAnalysisProfile(id, { signal }),
				loadProfileDefaults()
			]);
			if (signal?.aborted) return;
			defaults = catalog;
			reset(detail);
		} catch (e) {
			if (isAbortError(e) || signal?.aborted) return;
			profile = null;
			loadError = formatApiError(e, 'Could not load the profile.');
		} finally {
			if (!signal?.aborted) loading = false;
		}
	}

	$effect(() => {
		const id = profileId;
		const controller = new AbortController();
		void load(id, controller.signal);
		return () => controller.abort();
	});

	// ── URL-synced section ─────────────────────────────────────────────
	function parseSection(raw: string | null): ProfileEditorSection {
		return SECTIONS.includes(raw as ProfileEditorSection) ? (raw as ProfileEditorSection) : 'tools';
	}

	const section = $derived(parseSection(page.url.searchParams.get('section')));

	function setSection(next: ProfileEditorSection) {
		if (next === section) return;
		const url = new URL(page.url);
		if (next === 'tools') url.searchParams.delete('section');
		else url.searchParams.set('section', next);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// ── state ──────────────────────────────────────────────────────────
	const isLatest = $derived(!!profile && profile.version === profile.latest_version);
	const editable = $derived(
		!!profile && profile.is_owner && !profile.is_system && !profile.is_archived && isLatest
	);
	const canRestore = $derived(
		!!profile && profile.is_owner && !profile.is_system && !profile.is_archived && !isLatest
	);
	const latestVersionId = $derived(
		profile?.versions.find((v) => v.version === profile?.latest_version)?.id ?? null
	);

	const configDirty = $derived(
		!!profile && !!draft && stableStringify(draft) !== stableStringify(profile.config)
	);
	const metaDirty = $derived(
		!!profile && (name.trim() !== profile.name || description !== profile.description)
	);
	const dirty = $derived(editable && (configDirty || metaDirty));
	// An edit that normalizes to the stored config keeps the version.
	const analysisChanged = $derived(
		configDirty &&
			!(validation.status === 'valid' && validation.contentHash === profile?.content_hash)
	);
	const configBlocked = $derived(
		configDirty &&
			(invalid ||
				validation.status === 'pending' ||
				validation.status === 'invalid' ||
				validation.status === 'idle')
	);
	const canSave = $derived(dirty && !saving && !!name.trim() && !configBlocked);

	const editorErrors = $derived(
		Object.fromEntries(
			Object.entries(fieldErrors).filter(([key]) => key === 'config' || key.startsWith('config.'))
		)
	);

	async function save() {
		if (!profile || !draft || !canSave) return;
		saving = true;
		saveError = '';
		fieldErrors = {};
		const previousVersion = profile.version;
		try {
			const saved = await updateAnalysisProfile(profile.id, {
				name: name.trim(),
				description,
				...(configDirty ? { config: draft } : {})
			});
			if (saved.id !== profile.id) {
				toast.success(`Saved ${saved.name} as v${saved.version}`);
				reset(saved);
				await goto(`/analysis/profiles/${saved.id}${page.url.search}`, {
					replaceState: true,
					noScroll: true
				});
			} else {
				reset(saved);
				toast.success(
					saved.version === previousVersion && configDirty
						? `No analysis change — kept v${saved.version}`
						: `Saved ${saved.name}`
				);
			}
		} catch (e) {
			fieldErrors = apiFieldErrors(e);
			if (Object.keys(fieldErrors).length === 0)
				saveError = formatApiError(e, 'Could not save the profile.');
			else saveError = 'Fix the highlighted fields and save again.';
		} finally {
			saving = false;
		}
	}

	// Remounting the editor also drops input it could not apply (an
	// incomplete number, invalid provider JSON) that a reset would not touch.
	let editorKey = $state(0);

	function discard() {
		if (!profile) return;
		reset(profile);
		editorKey += 1;
	}

	// ── restore / archive / clone ──────────────────────────────────────
	let restoring = $state(false);

	async function restore() {
		if (!profile || !canRestore) return;
		restoring = true;
		try {
			const saved = await updateAnalysisProfile(profile.id, { config: profile.config });
			toast.success(
				saved.version === profile.latest_version
					? `v${profile.version} matches the latest version — nothing to restore`
					: `Restored v${profile.version} as v${saved.version}`
			);
			await goto(`/analysis/profiles/${saved.id}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not restore this version.'));
		} finally {
			restoring = false;
		}
	}

	let archiveOpen = $state(false);
	let archiving = $state(false);

	async function confirmArchive() {
		if (!profile) return;
		archiving = true;
		try {
			await archiveAnalysisProfile(profile.id);
			toast.success(`Archived ${profile.name}`);
			archiveOpen = false;
			skipGuard = true;
			await goto('/analysis/profiles');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not archive the profile.'));
		} finally {
			archiving = false;
		}
	}

	let unarchiving = $state(false);

	async function unarchive() {
		if (!profile) return;
		unarchiving = true;
		try {
			const restored = await unarchiveAnalysisProfile(profile.id);
			toast.success(`Restored ${restored.name}`);
			if (restored.id === profile.id) reset(restored);
			else await goto(`/analysis/profiles/${restored.id}`);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not restore the profile.'));
		} finally {
			unarchiving = false;
		}
	}

	let cloneOpen = $state(false);

	function onCloned(created: AnalysisProfile) {
		toast.success(`Created ${created.name}`);
		void goto(`/analysis/profiles/${created.id}`);
	}

	// ── unsaved changes guard ──────────────────────────────────────────
	let skipGuard = false;

	beforeNavigate((navigation) => {
		if (skipGuard) {
			skipGuard = false;
			return;
		}
		if (!dirty || saving) return;
		// Tab switches only rewrite the query string.
		if (navigation.to?.url.pathname === page.url.pathname) return;
		if (navigation.type === 'leave') {
			navigation.cancel();
			return;
		}
		if (!confirm('Discard your unsaved changes to this profile?')) navigation.cancel();
	});
</script>

<div class="space-y-6">
	<PageHeader
		title={profile?.name ?? 'Analysis profile'}
		description={profile?.description ||
			'Tools, AI reviewer, scope and outcomes of one profile version.'}
		back={{ href: '/analysis/profiles', label: 'Profiles' }}
	>
		{#snippet meta()}
			{#if profile}
				<span class="font-mono text-xs text-muted-foreground"
					>{profile.slug}@v{profile.version}</span
				>
				{#if profile.is_system}
					<Badge variant="secondary">System</Badge>
				{:else if profile.is_owner}
					<Badge variant="outline">Yours</Badge>
				{/if}
				{#if profile.is_archived}<Badge variant="secondary">Archived</Badge>{/if}
				{#if !isLatest}<Badge variant="outline">Older version</Badge>{/if}
				{#if dirty}<Badge>Unsaved changes</Badge>{/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if profile}
				<Button variant="outline" size="sm" onclick={() => (cloneOpen = true)}>
					<Copy />{profile.is_system ? 'Customize' : 'Clone'}
				</Button>
				{#if editable}
					<Button
						variant="outline"
						size="sm"
						class="hover:bg-destructive/10 hover:text-danger-fg"
						onclick={() => (archiveOpen = true)}
					>
						<Archive />Archive
					</Button>
				{/if}
				{#if canRestore}
					<Button size="sm" onclick={restore} disabled={restoring}>
						{#if restoring}<LoaderCircle
								class="animate-spin motion-reduce:animate-none"
							/>{:else}<History />{/if}
						Restore as latest
					</Button>
				{/if}
				{#if editable}
					{#if dirty}
						<Button variant="outline" size="sm" onclick={discard} disabled={saving}
							><Undo2 />Discard</Button
						>
					{/if}
					<Button size="sm" onclick={save} disabled={!canSave}>
						{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Save
							/>{/if}
						{analysisChanged ? `Save as v${profile.latest_version + 1}` : 'Save'}
					</Button>
				{/if}
			{/if}
		{/snippet}
	</PageHeader>

	{#if loading && !profile}
		<LoadingState label="loading profile" />
	{:else if loadError}
		<ErrorAlert message={loadError} onretry={() => load(profileId)} />
	{:else if profile && draft}
		{#if profile.is_system}
			<div
				class="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-sm"
				role="note"
			>
				<Lock class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
				<p class="text-muted-foreground">
					System profiles are read-only so every study can cite them unchanged.
					<button
						type="button"
						class="cursor-pointer rounded font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
						onclick={() => (cloneOpen = true)}>Customize a copy</button
					> to change it.
				</p>
			</div>
		{:else if profile.is_archived}
			<div
				class="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-sm"
				role="note"
			>
				<Archive class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
				<p class="text-muted-foreground">
					Archived: it can no longer be picked for new runs or studies.
					{#if profile.is_owner}
						<button
							type="button"
							class="cursor-pointer rounded font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-60"
							disabled={unarchiving}
							onclick={unarchive}>Restore it</button
						> to use it again (every version comes back).
					{:else}
						Clone it to use the configuration again.
					{/if}
				</p>
			</div>
		{:else if !isLatest}
			<div
				class="flex flex-wrap items-start gap-2 rounded-md border border-info/30 bg-info/10 px-3 py-2.5 text-sm"
				role="note"
			>
				<Info class="mt-0.5 size-4 shrink-0 text-info-fg" aria-hidden="true" />
				<p class="min-w-0 flex-1">
					<span class="font-medium text-info-fg">You are viewing v{profile.version}.</span>
					<span class="text-muted-foreground">
						Versions are immutable; runs pinned to it keep it.
						{#if latestVersionId}<a
								href="/analysis/profiles/{latestVersionId}"
								class="rounded font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
								>Open v{profile.latest_version}</a
							>{/if}{#if canRestore}, or restore this configuration as v{profile.latest_version +
								1}.{:else}.{/if}
					</span>
				</p>
			</div>
		{:else if !profile.is_owner}
			<div
				class="flex items-start gap-2 rounded-md border border-border bg-muted/40 px-3 py-2.5 text-sm"
				role="note"
			>
				<Lock class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
				<p class="text-muted-foreground">
					Another user's profile: read-only. Clone it to make your own.
				</p>
			</div>
		{/if}

		{#if saveError}<ErrorAlert message={saveError} />{/if}

		<div class="grid gap-6 xl:grid-cols-[minmax(0,1fr)_17rem]">
			<div class="min-w-0 space-y-6">
				{#if editable}
					<Card.Root>
						<Card.Header class="pb-3">
							<Card.Title class="text-base">Details</Card.Title>
							<Card.Description
								>Name and description change in place; they are not part of the analysis config.</Card.Description
							>
						</Card.Header>
						<Card.Content class="grid gap-4 sm:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
							<div class="space-y-1.5">
								<Label for="profile-name">Name</Label>
								<Input
									id="profile-name"
									bind:value={name}
									maxlength={200}
									autocomplete="off"
									aria-invalid={fieldErrors.name || !name.trim() ? true : undefined}
								/>
								{#if fieldErrors.name || !name.trim()}
									<p class="text-[11px] text-danger-fg">
										{fieldErrors.name || 'A name is required.'}
									</p>
								{/if}
							</div>
							<div class="space-y-1.5">
								<Label for="profile-description">Description</Label>
								<Textarea
									id="profile-description"
									bind:value={description}
									rows={2}
									maxlength={5000}
									class="min-h-9"
									placeholder="What this profile measures and why"
								/>
								{#if fieldErrors.description}<p class="text-[11px] text-danger-fg">
										{fieldErrors.description}
									</p>{/if}
							</div>
						</Card.Content>
					</Card.Root>
				{/if}

				<Card.Root>
					<Card.Content class="pt-4">
						{#key `${profile.id}:${editorKey}`}
							<ProfileEditor
								config={draft}
								{defaults}
								readonly={!editable}
								errors={editorErrors}
								bind:validation
								bind:invalid
								bind:section={() => section, setSection}
								onchange={(next) => (draft = next)}
							/>
						{/key}
					</Card.Content>
				</Card.Root>
			</div>

			<aside
				class="grid content-start gap-4 sm:grid-cols-2 xl:grid-cols-1"
				aria-label="Profile version"
			>
				<Card.Root>
					<Card.Header class="pb-2">
						<Card.Title class="text-sm">Version</Card.Title>
					</Card.Header>
					<Card.Content class="space-y-3 text-xs">
						<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1.5">
							<dt class="text-muted-foreground">Slug</dt>
							<dd class="break-all font-mono">{profile.slug}</dd>
							<dt class="text-muted-foreground">Version</dt>
							<dd class="font-mono">
								v{profile.version}
								<span class="text-muted-foreground">of {profile.latest_version}</span>
							</dd>
							<dt class="text-muted-foreground">Content hash</dt>
							<dd class="break-all font-mono" title={profile.content_hash}>
								{shortHash(profile.content_hash, 16)}
							</dd>
							<dt class="text-muted-foreground">Created</dt>
							<dd>{formatDate(profile.created_at)}</dd>
							<dt class="text-muted-foreground">Used by</dt>
							<dd class="tabular-nums">
								{profile.usage.runs} run{profile.usage.runs === 1 ? '' : 's'} · {profile.usage
									.studies} stud{profile.usage.studies === 1 ? 'y' : 'ies'}
							</dd>
						</dl>
						{#if editable && configDirty}
							<p
								class="rounded-md border border-border bg-muted/40 px-2.5 py-2 leading-relaxed text-muted-foreground"
								aria-live="polite"
							>
								{#if analysisChanged}
									Saving creates <span class="font-medium text-foreground"
										>v{profile.latest_version + 1}</span
									>{#if validation.status === 'valid' && validation.contentHash}{' '}<span
											class="font-mono">#{shortHash(validation.contentHash, 8)}</span
										>{/if}. Runs and studies on v{profile.version} keep it.
								{:else}
									These edits normalize to the stored configuration; saving keeps v{profile.version}.
								{/if}
							</p>
						{/if}
					</Card.Content>
				</Card.Root>
				<Card.Root>
					<Card.Header class="pb-2">
						<Card.Title class="flex items-center gap-1.5 text-sm"
							><History class="size-4" aria-hidden="true" />History</Card.Title
						>
					</Card.Header>
					<Card.Content class="px-3 pt-0">
						<ProfileVersionList
							versions={profile.versions}
							currentId={profile.id}
							latestVersion={profile.latest_version}
							label="Versions of {profile.name}"
						/>
					</Card.Content>
				</Card.Root>
			</aside>
		</div>
	{/if}
</div>

<ProfileCloneDialog bind:open={cloneOpen} {profile} oncloned={onCloned} />

<ConfirmDialog
	bind:open={archiveOpen}
	title="Archive {profile?.name ?? 'profile'}?"
	description="Every version is archived and can no longer be picked for new runs or studies. Runs and studies that used it keep their pinned version."
	confirmLabel="Archive"
	destructive
	busy={archiving}
	onConfirm={confirmArchive}
/>
