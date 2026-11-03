<script lang="ts">
	/**
	 * /library/[kind]/new — create v1 of a user-owned entity: name, slug, description, sharing, then the
	 * kind's structured editor. The draft is validated by the server as you type (lint issues and the
	 * content hash); Create is enabled when nothing blocks it.
	 */
	import { goto } from '$app/navigation';
	import { untrack } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Card from '$lib/components/ui/card';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { apiErrorStatus, formatApiError } from '$lib/api/core';
	import { createLibraryItem, getLibraryVersion } from '$lib/api/v3/library';
	import type { LibraryContentMap, LibraryKind, PolicyTier } from '$lib/api/v3/types';
	import { POLICY_TIERS, parseRef } from '$lib/api/v3/types';
	import { ContentEditor, invalidateLibrary, isValidSlug, kindInfo, libraryHref, libraryStore, slugify, tierLabel } from '$lib/components/library';
	import FormRow from '$lib/components/library/FormRow.svelte';
	import IssueList from '$lib/components/library/IssueList.svelte';
	import { blankContent } from '$lib/components/library/skeletons';
	import { ContentValidation } from '$lib/components/library/validation.svelte';
	import type { DetectorContent } from '$lib/api/v3/types';
	import type { PageProps } from './$types';

	let { data }: PageProps = $props();

	const info = $derived(kindInfo(data.kind));
	const kind = $derived<LibraryKind>(info.kind);

	let name = $state('');
	let slug = $state('');
	let slugTouched = $state(false);
	let description = $state('');
	let visibility = $state<'private' | 'shared'>('private');
	let content = $state<LibraryContentMap[LibraryKind] | null>(null);
	let editorErrors = $state<Record<string, string>>({});
	let loadError = $state('');
	let tier = $state<PolicyTier>(untrack(() => data.tier as PolicyTier));

	const validation = new ContentValidation();

	// Initial content: a copy of ?from, a tier preset, or the blank skeleton.
	$effect(() => {
		const k = kind;
		const from = data.from;
		untrack(() => {
			content = null;
			loadError = '';
			if (from) {
				const ref = parseRef(from)!;
				getLibraryVersion(k, ref.slug, ref.version)
					.then((item) => {
						content = item.content;
						name = `${item.name} (copy)`;
						description = item.description;
					})
					.catch((e) => (loadError = formatApiError(e, `Could not load ${from}.`)));
			} else if (k === 'resource_policy') {
				void libraryStore.ensureKnobs().then((knobs) => {
					if (knobs) content = $state.snapshot(knobs.tiers[tier]) as LibraryContentMap[LibraryKind];
					else loadError = libraryStore.knobsError;
				});
			} else {
				const blank = blankContent(k);
				content = blank && k === 'detector' && data.prefill ? ({ ...blank, ...data.prefill } as DetectorContent) : blank;
			}
		});
	});

	$effect(() => {
		if (!slugTouched) slug = slugify(name);
	});

	$effect(() => {
		if (content) validation.schedule(kind, $state.snapshot(content));
	});

	function pickTier(t: string) {
		tier = t as PolicyTier;
		const knobs = libraryStore.knobs;
		if (knobs) content = $state.snapshot(knobs.tiers[tier]) as LibraryContentMap[LibraryKind];
	}

	let saving = $state(false);
	let saveError = $state('');
	let slugError = $state('');

	const blocked = $derived(
		!content || !isValidSlug(slug) || Object.keys(editorErrors).length > 0 || validation.errors.length > 0 || validation.validating,
	);

	async function create() {
		if (!content) return;
		saving = true;
		saveError = '';
		slugError = '';
		try {
			const item = await createLibraryItem(kind, { slug, name: name || slug, description, content: $state.snapshot(content), visibility });
			invalidateLibrary(kind);
			await goto(libraryHref(kind, item.slug));
		} catch (e) {
			if (apiErrorStatus(e) === 409) slugError = `The slug "${slug}" is taken. Choose another.`;
			else saveError = formatApiError(e, 'Could not create it.');
		} finally {
			saving = false;
		}
	}
</script>

<div class="space-y-4">
	<PageHeader title="New {info.label.toLowerCase()}" description={info.description} back={{ href: `/library/${info.api}`, label: info.plural }}>
		{#snippet actions()}
			<Button size="sm" onclick={create} disabled={blocked || saving}>
				{#if saving}<LoaderCircle class="animate-spin" />{/if}Create v1
			</Button>
		{/snippet}
	</PageHeader>

	{#if info.editing === 'readonly'}
		<ErrorAlert message="{info.plural} are seeded and cannot be created here. Import a YAML package instead." />
	{:else}
		<Card.Root class="py-4">
			<Card.Content class="grid gap-4 px-4 md:grid-cols-2">
				<FormRow label="Name" for="new-name">
					<Input id="new-name" bind:value={name} placeholder="A short display name" />
				</FormRow>
				<FormRow label="Slug" for="new-slug" help="Permanent id: studies pin slug@version." error={slugError || (slug && !isValidSlug(slug) ? 'Letters, digits, _ and -; start with a letter or digit.' : '')}>
					<Input id="new-slug" bind:value={slug} oninput={() => (slugTouched = true)} class="font-mono" />
				</FormRow>
				<FormRow label="Description" for="new-desc" class="md:col-span-2">
					<Input id="new-desc" bind:value={description} />
				</FormRow>
				<FormRow label="Visibility" help="Shared entities are visible to every user (read-only for them).">
					<Segmented aria-label="Visibility" value={visibility} items={[{ value: 'private', label: 'Private' }, { value: 'shared', label: 'Shared' }]} onValueChange={(v) => (visibility = v === 'shared' ? 'shared' : 'private')} />
				</FormRow>
				{#if kind === 'resource_policy' && !data.from}
					<FormRow label="Start from tier" help="Every knob starts at that tier's value; your changes show as overrides.">
						<Segmented aria-label="Tier" value={tier} items={POLICY_TIERS.map((t) => ({ value: t, label: tierLabel(t) }))} onValueChange={pickTier} />
					</FormRow>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if loadError}<ErrorAlert message={loadError} />{/if}
		{#if saveError}<ErrorAlert message={saveError} />{/if}

		<div class="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
			<Card.Root class="py-4">
				<Card.Content class="px-4">
					{#if content}
						<ContentEditor
							{kind}
							value={content}
							tier={kind === 'resource_policy' ? tier : null}
							prefill={null}
							onchange={(c, errors) => {
								content = c;
								editorErrors = errors;
							}}
						/>
					{:else if !loadError}
						<LoadingState label="preparing the draft" />
					{/if}
				</Card.Content>
			</Card.Root>
			<aside class="space-y-3 xl:sticky xl:top-20 xl:self-start">
				<Card.Root class="gap-2 py-3">
					<Card.Header class="px-4"><Card.Title class="text-sm">Validation</Card.Title></Card.Header>
					<Card.Content class="space-y-2 px-4">
						{#if validation.validating}
							<p class="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle class="size-3.5 animate-spin" />Checking</p>
						{/if}
						{#each Object.entries(editorErrors) as [path, message] (path)}
							<p class="text-xs text-danger-fg"><span class="font-mono">{path}</span> {message}</p>
						{/each}
						<IssueList issues={validation.issues} emptyText={validation.ok ? 'No issues.' : undefined} />
						{#if validation.contentHash}
							<p class="font-mono text-[11px] text-muted-foreground">content #{validation.contentHash.slice(0, 12)}</p>
						{/if}
					</Card.Content>
				</Card.Root>
			</aside>
		</div>
	{/if}
</div>
