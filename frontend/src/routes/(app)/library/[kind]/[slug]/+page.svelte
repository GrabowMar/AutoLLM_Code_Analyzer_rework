<script lang="ts">
	/**
	 * /library/[kind]/[slug] — one entity: the shown version (latest, or `?v=` read-only), its versions,
	 * diffs between any two, where it is used, and (tasks) the oracle status per stack. Owners (and staff
	 * for system rows) edit the latest version; saving creates the next version (immutable versions,
	 * D8). Others fork it into their own slug. Archive hides the slug from pickers; nothing is deleted.
	 */
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { untrack } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Tabs from '$lib/components/ui/tabs';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import Archive from '@lucide/svelte/icons/archive';
	import ArchiveRestore from '@lucide/svelte/icons/archive-restore';
	import GitFork from '@lucide/svelte/icons/git-fork';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Package from '@lucide/svelte/icons/package';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Save from '@lucide/svelte/icons/save';
	import Share2 from '@lucide/svelte/icons/share-2';
	import { apiErrorStatus, formatApiError } from '$lib/api/core';
	import {
		archiveLibraryItem,
		createLibraryVersion,
		forkLibraryItem,
		getLibraryDetail,
		getLibraryDiff,
		getLibraryUsage,
		getLibraryVersion,
		getTaskOracle,
		setLibraryVisibility,
	} from '$lib/api/v3/library';
	import type {
		LibraryContentMap,
		LibraryDetailOut,
		LibraryDiffOut,
		LibraryItemOut,
		LibraryKind,
		ResourcePolicyContent,
		TaskDerived,
		TaskOracleEntry,
		UsageOut,
	} from '$lib/api/v3/types';
	import {
		ContentEditor,
		PackagesDialog,
		PinnedRefBadge,
		VersionDiff,
		VersionHistory,
		baseTier,
		canEdit,
		invalidateLibrary,
		isValidSlug,
		kindInfo,
		libraryHref,
		libraryStore,
		slugify,
		summaryLine,
		tierLabel,
	} from '$lib/components/library';
	import FormRow from '$lib/components/library/FormRow.svelte';
	import IssueList from '$lib/components/library/IssueList.svelte';
	import { ContentValidation } from '$lib/components/library/validation.svelte';
	import { jsonEqual } from '$lib/components/library/policy';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const info = $derived(kindInfo(data.kind));
	const kind = $derived<LibraryKind>(info.kind);

	// ── load ──
	let detail = $state<LibraryDetailOut | null>(null);
	let shown = $state<LibraryItemOut | null>(null);
	let loadError = $state('');
	let reloadKey = $state(0);

	$effect(() => {
		const k = kind;
		const slug = data.slug;
		const version = data.version;
		void reloadKey;
		const controller = new AbortController();
		untrack(() => {
			loadError = '';
			if (!detail || detail.latest.slug !== slug) {
				detail = null;
				shown = null;
			}
		});
		(async () => {
			try {
				const d = await getLibraryDetail(k, slug, { signal: controller.signal });
				const s = version && version !== d.latest.version ? await getLibraryVersion(k, slug, version, { signal: controller.signal }) : d.latest;
				detail = d;
				shown = s;
			} catch (e) {
				if (controller.signal.aborted) return;
				loadError = apiErrorStatus(e) === 404 ? `No ${info.label.toLowerCase()} "${slug}" (or it is not visible to you).` : formatApiError(e, 'Could not load it.');
			}
		})();
		return () => controller.abort();
	});

	$effect(() => void libraryStore.ensureMe());
	const me = $derived(libraryStore.me);
	const latest = $derived(detail?.latest ?? null);
	const isLatest = $derived(!!shown && !!latest && shown.version === latest.version);
	const editable = $derived(!!latest && info.editing === 'editable' && canEdit(latest, me));

	// ── tabs (URL-synced) ──
	let tab = $state(untrack(() => (data.diff ? 'diff' : data.tab)));
	function setParam(updates: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [k, v] of Object.entries(updates)) {
			if (v === null) url.searchParams.delete(k);
			else url.searchParams.set(k, v);
		}
		void goto(url, { replaceState: true, noScroll: true, keepFocus: true });
	}
	$effect(() => {
		const t = tab;
		untrack(() => {
			if ((page.url.searchParams.get('tab') ?? 'content') !== t && t !== 'diff') setParam({ tab: t === 'content' ? null : t });
		});
	});

	// ── editing ──
	let editing = $state(untrack(() => data.edit));
	let draft = $state<LibraryContentMap[LibraryKind] | null>(null);
	let draftName = $state('');
	let draftDescription = $state('');
	let editorErrors = $state<Record<string, string>>({});
	const validation = new ContentValidation();
	let saving = $state(false);
	let saveError = $state('');
	let stale = $state(false);
	let notice = $state('');

	function startEdit() {
		if (!latest) return;
		draft = $state.snapshot(latest.content) as LibraryContentMap[LibraryKind];
		draftName = latest.name;
		draftDescription = latest.description;
		editorErrors = {};
		saveError = '';
		stale = false;
		notice = '';
		editing = true;
		tab = 'content';
		if (data.version) setParam({ v: null, edit: '1' });
	}

	$effect(() => {
		if (editing && latest && !draft && isLatest) untrack(startEdit);
	});

	function cancelEdit() {
		editing = false;
		draft = null;
		validation.cancel();
		setParam({ edit: null });
	}

	$effect(() => {
		if (editing && draft) validation.schedule(kind, $state.snapshot(draft), latest?.is_system ?? false);
	});

	const dirty = $derived(
		!!draft && !!latest && (!jsonEqual($state.snapshot(draft), $state.snapshot(latest.content)) || draftName !== latest.name || draftDescription !== latest.description),
	);
	const sameHash = $derived(!!latest && validation.contentHash === latest.content_hash);
	const canSave = $derived(
		editing && dirty && !saving && Object.keys(editorErrors).length === 0 && validation.errors.length === 0 && !validation.validating,
	);

	async function save() {
		if (!latest || !draft) return;
		saving = true;
		saveError = '';
		notice = '';
		try {
			const item = await createLibraryVersion(kind, latest.slug, {
				base_version: latest.version,
				content: $state.snapshot(draft),
				name: draftName !== latest.name ? draftName : null,
				description: draftDescription !== latest.description ? draftDescription : null,
			});
			invalidateLibrary(kind);
			editing = false;
			draft = null;
			notice = item.unchanged ? `No new version: the content hashes equal to v${item.version}.` : `Saved as v${item.version}.`;
			setParam({ edit: null, v: null });
			reloadKey++;
		} catch (e) {
			const body = e as { code?: string };
			if (apiErrorStatus(e) === 409 && body.code === 'stale_base') {
				stale = true;
				saveError = `Someone saved a newer version of ${latest.slug} while you were editing. Reload to see it; your draft is kept until you leave.`;
			} else {
				saveError = formatApiError(e, 'Could not save the new version.');
			}
		} finally {
			saving = false;
		}
	}

	async function reloadKeepingDraft() {
		stale = false;
		saveError = '';
		reloadKey++;
	}

	// ── fork ──
	let forkOpen = $state(false);
	let forkSlug = $state('');
	let forkName = $state('');
	let forkError = $state('');
	let forking = $state(false);

	function openFork() {
		if (!shown) return;
		forkName = `${shown.name} (mine)`;
		forkSlug = slugify(`${shown.slug}-mine`);
		forkError = '';
		forkOpen = true;
	}

	async function fork() {
		if (!shown) return;
		forking = true;
		forkError = '';
		try {
			const item = await forkLibraryItem(kind, shown.slug, { new_slug: forkSlug, name: forkName, version: shown.version });
			invalidateLibrary(kind);
			forkOpen = false;
			await goto(`${libraryHref(kind, item.slug)}?edit=1`);
		} catch (e) {
			forkError = apiErrorStatus(e) === 409 ? `The slug "${forkSlug}" is taken.` : formatApiError(e, 'Could not fork it.');
		} finally {
			forking = false;
		}
	}

	// ── archive / visibility ──
	let confirmArchive = $state(false);
	let actionBusy = $state(false);
	let actionError = $state('');

	async function toggleArchive() {
		if (!latest) return;
		actionBusy = true;
		actionError = '';
		try {
			await archiveLibraryItem(kind, latest.slug, !latest.is_archived);
			invalidateLibrary(kind);
			reloadKey++;
		} catch (e) {
			actionError = formatApiError(e, 'The change failed.');
		} finally {
			actionBusy = false;
			confirmArchive = false;
		}
	}

	async function toggleShare() {
		if (!latest) return;
		actionBusy = true;
		actionError = '';
		try {
			await setLibraryVisibility(kind, latest.slug, latest.visibility === 'shared' ? 'private' : 'shared');
			invalidateLibrary(kind);
			reloadKey++;
		} catch (e) {
			actionError = formatApiError(e, 'The change failed.');
		} finally {
			actionBusy = false;
		}
	}

	// ── usage ──
	let usage = $state<UsageOut | null>(null);
	let usageError = $state('');
	$effect(() => {
		const k = kind;
		const slug = data.slug;
		void reloadKey;
		getLibraryUsage(k, slug)
			.then((u) => (usage = u))
			.catch((e) => (usageError = formatApiError(e, 'Could not load where it is used.')));
	});
	const inUse = $derived(!!usage && (usage.studies.length > 0 || usage.runs > 0));

	// ── diff ──
	let diffPair = $state<[number, number] | null>(untrack(() => data.diff));
	let diff = $state<LibraryDiffOut | null>(null);
	let diffError = $state('');
	$effect(() => {
		const pair = diffPair;
		if (!pair) return;
		diff = null;
		diffError = '';
		getLibraryDiff(kind, data.slug, pair[0], pair[1])
			.then((d) => (diff = d))
			.catch((e) => (diffError = formatApiError(e, 'Could not load the diff.')));
	});
	function showDiff(a: number, b: number) {
		diffPair = [a, b];
		tab = 'diff';
		setParam({ diff: `${a}..${b}`, tab: null });
	}

	// ── oracle (tasks) ──
	let oracle = $state<Record<string, TaskOracleEntry> | null>(null);
	$effect(() => {
		if (kind !== 'task' || !shown) return;
		getTaskOracle(shown.slug, shown.version)
			.then((o) => (oracle = o))
			.catch(() => (oracle = {}));
	});

	// ── misc ──
	let packagesOpen = $state(false);
	const policySummary = $derived.by(() => {
		if (kind !== 'resource_policy' || !shown || !libraryStore.knobs) return '';
		const tier = String(shown.columns.tier ?? 'custom');
		const knobs = libraryStore.knobs;
		const content = shown.content as ResourcePolicyContent;
		// Custom presets are compared against the closest tier (backend `diff_from_tier(content, None)`).
		const base = knobs.tiers[baseTier(tier, content, knobs.tiers, knobs.knobs)];
		return summaryLine(content, { name: shown.name, version: shown.version, tier, base, tiers: knobs.tiers, knobs: knobs.knobs });
	});
	$effect(() => {
		if (kind === 'resource_policy') void libraryStore.ensureKnobs();
	});
	const date = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
</script>

<div class="space-y-4">
	{#if loadError}
		<PageHeader title={data.slug} back={{ href: `/library/${info.api}`, label: info.plural }} />
		<ErrorAlert message={loadError} onretry={() => reloadKey++} />
	{:else if !shown || !latest || !detail}
		<LoadingState label="loading {info.label.toLowerCase()}" />
	{:else}
		<PageHeader title={shown.name || shown.slug} description={shown.description || info.description} back={{ href: `/library/${info.api}`, label: info.plural }}>
			{#snippet leading()}<info.icon class="size-5 text-primary" aria-hidden="true" />{/snippet}
			{#snippet meta()}
				<PinnedRefBadge ref={shown!.pinned_ref} {kind} latestVersion={latest!.version} href="" />
				{#if shown!.is_system}<StatusBadge status="system" tone="neutral" />{/if}
				{#if latest!.visibility === 'shared'}<StatusBadge status="shared" tone="info" />{/if}
				{#if latest!.is_archived}<StatusBadge status="archived" tone="warning" />{/if}
				{#if kind === 'resource_policy'}<StatusBadge status={String(shown!.columns.tier ?? 'custom')} label={tierLabel(String(shown!.columns.tier ?? 'custom'))} tone="neutral" />{/if}
			{/snippet}
			{#snippet actions()}
				<Button variant="outline" size="sm" onclick={() => (packagesOpen = true)}><Package />Export</Button>
				{#if info.editing === 'editable'}
					<Button variant="outline" size="sm" onclick={openFork}><GitFork />Fork</Button>
				{/if}
				{#if editable && !latest!.is_system}
					<Button variant="outline" size="sm" onclick={toggleShare} disabled={actionBusy}><Share2 />{latest!.visibility === 'shared' ? 'Unshare' : 'Share'}</Button>
				{/if}
				{#if editable}
					<Button variant="outline" size="sm" onclick={() => (latest!.is_archived ? toggleArchive() : (confirmArchive = true))} disabled={actionBusy}>
						{#if latest!.is_archived}<ArchiveRestore />Restore{:else}<Archive />Archive{/if}
					</Button>
				{/if}
				{#if editing}
					<Button variant="outline" size="sm" onclick={cancelEdit}>Cancel</Button>
					<Button size="sm" onclick={save} disabled={!canSave}>
						{#if saving}<LoaderCircle class="animate-spin" />{:else}<Save />{/if}Save as v{latest!.version + 1}
					</Button>
				{:else if editable}
					<Button size="sm" onclick={startEdit}><Pencil />{isLatest ? 'Edit' : `Edit latest (v${latest!.version})`}</Button>
				{/if}
			{/snippet}
			<div class="header-stats">
				<span class="header-stat"><b>v{shown.version}</b> of {latest.version}</span>
				<span class="header-stat font-mono">#{shown.content_hash.slice(0, 12)}</span>
				<span class="header-stat">{shown.created_by?.username ?? 'system'} · {date(shown.created_at)}</span>
				{#if shown.derived_from}<span class="header-stat">from <span class="font-mono">{shown.derived_from}</span></span>{/if}
				{#if usage}<span class="header-stat"><b>{usage.studies.length}</b> studies · <b>{usage.runs}</b> runs</span>{/if}
			</div>
		</PageHeader>

		{#if policySummary}<p class="font-mono text-xs text-muted-foreground">{policySummary}</p>{/if}
		{#if notice}<p class="text-sm text-success-fg" role="status">{notice}</p>{/if}
		{#if actionError}<ErrorAlert message={actionError} />{/if}
		{#if saveError}
			<ErrorAlert message={saveError} onretry={stale ? reloadKeepingDraft : undefined} />
		{/if}
		{#if !isLatest && !editing}
			<p class="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
				You are looking at <b>v{shown.version}</b> (read-only). The latest is <a class="text-primary hover:underline" href={libraryHref(kind, shown.slug)}>v{latest.version}</a>
				· <button type="button" class="cursor-pointer text-primary hover:underline" onclick={() => showDiff(shown!.version, latest!.version)}>compare</button>
			</p>
		{/if}
		{#if !editable && info.editing === 'editable' && !editing}
			<p class="text-xs text-muted-foreground">
				{latest.is_system ? 'System entities are seeded and read-only.' : 'Only its owner edits it.'} Fork it to make your own copy (it records where it came from).
			</p>
		{/if}

		<Tabs.Root bind:value={tab}>
			<Tabs.List>
				<Tabs.Trigger value="content">{editing ? 'Edit' : 'Content'}</Tabs.Trigger>
				<Tabs.Trigger value="versions">Versions <span class="font-mono text-[11px] text-muted-foreground">{detail.versions.length}</span></Tabs.Trigger>
				{#if diffPair}<Tabs.Trigger value="diff">Diff v{diffPair[0]} → v{diffPair[1]}</Tabs.Trigger>{/if}
				<Tabs.Trigger value="usage">Usage</Tabs.Trigger>
				{#if kind === 'task'}<Tabs.Trigger value="oracle">Oracle</Tabs.Trigger>{/if}
			</Tabs.List>

			<Tabs.Content value="content" class="pt-4">
				{#if editing && draft}
					<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
						<div class="space-y-4">
							<Card.Root class="py-4">
								<Card.Content class="grid gap-4 px-4 md:grid-cols-2">
									<FormRow label="Name" for="edit-name"><Input id="edit-name" bind:value={draftName} /></FormRow>
									<FormRow label="Description" for="edit-desc"><Input id="edit-desc" bind:value={draftDescription} /></FormRow>
								</Card.Content>
							</Card.Root>
							<Card.Root class="py-4">
								<Card.Content class="px-4">
									<ContentEditor
										{kind}
										value={draft}
										base={latest.content}
										slug={latest.slug}
										version={latest.version}
										tier={String(latest.columns.tier ?? '') || null}
										derived={(latest.derived as TaskDerived | null) ?? null}
										issues={latest.lint?.issues ?? []}
										warnOnFingerprintChange={inUse}
										onchange={(c, errors) => {
											draft = c;
											editorErrors = errors;
										}}
									/>
								</Card.Content>
							</Card.Root>
						</div>
						<aside class="space-y-3 xl:sticky xl:top-20 xl:self-start">
							<Card.Root class="gap-2 py-3">
								<Card.Header class="px-4"><Card.Title class="text-sm">Before saving</Card.Title></Card.Header>
								<Card.Content class="space-y-2 px-4 text-xs">
									<p class="text-muted-foreground">Saving creates v{latest.version + 1}; v{latest.version} stays exactly as it is for the studies that pin it.</p>
									{#if inUse}
										<p class="text-warning-fg">Used by {usage?.studies.length} studies and {usage?.runs} runs: they keep their pinned version. New studies get the new one.</p>
									{/if}
									{#if validation.validating}
										<p class="flex items-center gap-2 text-muted-foreground"><LoaderCircle class="size-3.5 animate-spin" />Checking</p>
									{/if}
									{#each Object.entries(editorErrors) as [path, message] (path)}
										<p class="text-danger-fg"><span class="font-mono">{path}</span> {message}</p>
									{/each}
									<IssueList issues={validation.issues} emptyText={validation.ok ? 'No lint issues.' : undefined} />
									{#if validation.contentHash}
										<p class="font-mono text-[11px] text-muted-foreground">
											content #{validation.contentHash.slice(0, 12)}{sameHash ? ' · same as the base (nothing to save)' : ''}
										</p>
									{/if}
								</Card.Content>
							</Card.Root>
						</aside>
					</div>
				{:else}
					<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
						<Card.Root class="py-4">
							<Card.Content class="px-4">
								<ContentEditor
									{kind}
									value={shown.content}
									readonly
									slug={shown.slug}
									version={shown.version}
									tier={String(shown.columns.tier ?? '') || null}
									derived={(shown.derived as TaskDerived | null) ?? null}
									issues={shown.lint?.issues ?? []}
									onchange={() => {}}
								/>
							</Card.Content>
						</Card.Root>
						<aside class="space-y-3">
							<Card.Root class="gap-2 py-3">
								<Card.Header class="px-4"><Card.Title class="text-sm">Lint</Card.Title></Card.Header>
								<Card.Content class="px-4">
									<IssueList issues={shown.lint?.issues ?? []} emptyText={shown.lint ? `Status ${shown.lint.status}.` : 'Not linted.'} />
								</Card.Content>
							</Card.Root>
							{#if shown.tags.length}
								<Card.Root class="gap-2 py-3">
									<Card.Header class="px-4"><Card.Title class="text-sm">Tags</Card.Title></Card.Header>
									<Card.Content class="flex flex-wrap gap-1 px-4">
										{#each shown.tags as t (t)}<span class="rounded-sm border border-border px-1 font-mono text-[11px]">{t}</span>{/each}
									</Card.Content>
								</Card.Root>
							{/if}
						</aside>
					</div>
				{/if}
			</Tabs.Content>

			<Tabs.Content value="versions" class="pt-4">
				<VersionHistory {kind} slug={latest.slug} versions={detail.versions} current={shown.version} latest={latest.version} ondiff={showDiff} />
			</Tabs.Content>

			{#if diffPair}
				<Tabs.Content value="diff" class="pt-4">
					{#if diffError}
						<ErrorAlert message={diffError} />
					{:else if !diff}
						<LoadingState label="loading the diff" />
					{:else}
						<VersionDiff unified={diff.unified} structured={diff.structured} labels={[`v${diffPair[0]}`, `v${diffPair[1]}`]} />
					{/if}
				</Tabs.Content>
			{/if}

			<Tabs.Content value="usage" class="space-y-3 pt-4">
				{#if usageError}
					<ErrorAlert message={usageError} />
				{:else if !usage}
					<LoadingState label="loading usage" />
				{:else if usage.studies.length === 0 && usage.runs === 0}
					<p class="text-sm text-muted-foreground">No study or run uses any version of it yet.</p>
				{:else}
					<p class="text-sm"><b class="font-mono">{usage.runs}</b> runs used a version of it.</p>
					{#if usage.studies.length}
						<div class="overflow-x-auto rounded-md border border-border">
							<table class="w-full text-sm">
								<thead><tr><th>Study</th><th>Status</th><th class="num">Version</th></tr></thead>
								<tbody>
									{#each usage.studies as s (s.id + s.version)}
										<tr>
											<td><a class="hover:text-primary hover:underline" href="/studies/{s.id}">{s.name}</a></td>
											<td><StatusBadge status={s.status} /></td>
											<td class="num font-mono">v{s.version}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				{/if}
			</Tabs.Content>

			{#if kind === 'task'}
				<Tabs.Content value="oracle" class="space-y-3 pt-4">
					<p class="text-xs text-muted-foreground">
						Whether a reference implementation of v{shown.version} passes its own evaluation suite on each stack (written only by oracle validation). A missing entry means not measured, never passed.
					</p>
					{#if oracle === null}
						<LoadingState label="loading the oracle status" />
					{:else if Object.keys(oracle).length === 0}
						<p class="text-sm text-muted-foreground">Not measured on any stack yet.</p>
					{:else}
						<div class="overflow-x-auto rounded-md border border-border">
							<table class="w-full text-sm">
								<thead><tr><th>Stack</th><th>Status</th><th>Suite</th><th>When</th></tr></thead>
								<tbody>
									{#each Object.entries(oracle) as [stack, entry] (stack)}
										<tr>
											<td><PinnedRefBadge ref={stack} kind="stack" /></td>
											<td><StatusBadge status={entry.status} /></td>
											<td class="font-mono text-xs">{entry.eval_suite_ref ?? '—'}</td>
											<td class="text-xs">{entry.at ? date(entry.at) : '—'}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				</Tabs.Content>
			{/if}
		</Tabs.Root>
	{/if}
</div>

{#if shown}
	<PackagesDialog bind:open={packagesOpen} exportRefs={[`${kind}:${shown.slug}@${shown.version}`]} />
{/if}

<ConfirmDialog
	bind:open={confirmArchive}
	title="Archive {latest?.slug ?? ''}?"
	description="Archived entities disappear from pickers and lists (unless “Archived” is ticked). Every version is kept, and studies that pin one keep working. You can restore it any time."
	confirmLabel="Archive"
	busy={actionBusy}
	onConfirm={toggleArchive}
/>

<Dialog.Root bind:open={forkOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Fork {shown?.slug}@{shown?.version}</Dialog.Title>
			<Dialog.Description>Creates v1 of your own slug with this content; it records which version it came from.</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-3">
			<FormRow label="Name" for="fork-name"><Input id="fork-name" bind:value={forkName} /></FormRow>
			<FormRow label="Slug" for="fork-slug" error={forkError || (forkSlug && !isValidSlug(forkSlug) ? 'Letters, digits, _ and -.' : '')}>
				<Input id="fork-slug" bind:value={forkSlug} class="font-mono" />
			</FormRow>
		</div>
		<Dialog.Footer>
			<Button variant="outline" size="sm" onclick={() => (forkOpen = false)}>Cancel</Button>
			<Button size="sm" onclick={fork} disabled={forking || !isValidSlug(forkSlug)}>
				{#if forking}<LoaderCircle class="animate-spin" />{/if}Fork and edit
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
