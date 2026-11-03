<script lang="ts">
	// The Study builder (spec 10.2). `?draft=<id>` resumes a saved draft; `?clone=<id>[&clone_mode=latest_versions]`
	// starts from a copy (exact pins by default); `?mode=slot&slot_sets=a,b&targets=x,y` pre-fills a slot study
	// from the slot picker; `?model=<id>` or `?models=a,b` (endpoint page) pre-fills the model factor. The first save puts `?draft=<id>` in the URL, so a reload resumes the same draft.
	import { onDestroy, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { formatApiError } from '$lib/api/core';
	import { cloneStudy, getStudy } from '$lib/api/v3/studies';
	import type { StudyMode, StudyOut } from '$lib/api/v3/types';
	import { BuilderState } from '$lib/components/studies/builder/builder.svelte';
	import StudyBuilder from '$lib/components/studies/builder/StudyBuilder.svelte';
	import { defaultDesign, setLevels } from '$lib/components/studies/builder/design';
	import { loadModelCatalog, modelLevelsFromRefs } from '$lib/components/studies/shared/catalog';
	import { isEditable } from '$lib/components/studies/format';

	let builder = $state<BuilderState | null>(null);
	let error = $state('');
	let versionDiffs = $state<{ factor: string; from: string; to: string }[]>([]);
	let unknownModels = $state<string[]>([]);
	let started = '';

	const list = (key: string) =>
		(page.url.searchParams.get(key) ?? '')
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);

	function onCreated(study: StudyOut) {
		const url = new URL(page.url);
		url.search = `?draft=${study.id}`;
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		started = `draft:${study.id}`;
	}

	async function init(key: string) {
		started = key; // the clone branch and the first save move it on to `draft:<id>`
		error = '';
		unknownModels = [];
		builder?.destroy();
		builder = null;
		const params = page.url.searchParams;
		try {
			const draftId = params.get('draft');
			const cloneId = params.get('clone');
			if (draftId) {
				const study = await getStudy(draftId);
				if (!isEditable(study.status, study.locked)) {
					await goto(`/studies/${study.id}`, { replaceState: true });
					return;
				}
				const b = new BuilderState(study.design, {}, { onCreated });
				b.attach(study);
				builder = b;
			} else if (cloneId) {
				const source = await getStudy(cloneId);
				const mode = params.get('clone_mode') === 'latest_versions' ? 'latest_versions' : 'exact';
				const copy = await cloneStudy(cloneId, { mode, name: `${source.name} (copy)` });
				versionDiffs = copy.version_diffs ?? [];
				const b = new BuilderState(copy.design, {}, { onCreated });
				b.attach(copy);
				builder = b;
				onCreated(copy);
			} else {
				const mode: StudyMode = params.get('mode') === 'slot' ? 'slot' : 'app';
				let design = defaultDesign(mode);
				if (mode === 'slot') design.units = { tasks: [], slot_sets: list('slot_sets'), slot_targets: list('targets') };
				// `?model=` (model page) and `?models=a,b` (endpoint page): canonical slugs, model ids or pks.
				const refs = [...list('model'), ...list('models')];
				if (refs.length) {
					let resolved = modelLevelsFromRefs(await loadModelCatalog().catch(() => []), refs);
					if (resolved.unknown.length) {
						// A hidden local model picked on its endpoint page is only in the full list.
						resolved = modelLevelsFromRefs(await loadModelCatalog({ includeUnavailable: true }).catch(() => []), refs);
					}
					unknownModels = resolved.unknown;
					if (resolved.levels.length) design = setLevels(design, 'model', resolved.levels);
				}
				builder = new BuilderState(design, {}, { onCreated });
			}
		} catch (e) {
			error = formatApiError(e, 'Could not open the study.');
		}
	}

	// A fresh `/studies/new` (or another draft) re-initialises; our own `?draft=` replacement does not.
	$effect(() => {
		const key = page.url.searchParams.get('draft') ? `draft:${page.url.searchParams.get('draft')}` : page.url.search;
		untrack(() => {
			if (key !== started) void init(key);
		});
	});

	onDestroy(() => builder?.destroy());

	const title = $derived(builder?.study ? `Design: ${builder.study.name}` : 'New study');
</script>

<div class="space-y-4">
	<PageHeader {title} description="Design the study, inspect every prompt and the cost, then freeze the plan and start." back={{ href: '/studies', label: 'Studies' }}>
		{#snippet meta()}
			{#if builder?.study}<StatusBadge status={builder.study.status} />{/if}
		{/snippet}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => init(page.url.search)} />{/if}
	{#if unknownModels.length > 0}
		<p class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
			Not in the model catalog, left out: <span class="font-mono">{unknownModels.join(', ')}</span>.
		</p>
	{/if}
	{#if versionDiffs.length > 0}
		<p class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs">
			Cloned with the latest versions:
			{#each versionDiffs as d, i (d.factor + d.from)}{i ? '; ' : ' '}<span class="font-mono">{d.factor}</span> {d.from} → {d.to}{/each}.
			These conditions are not comparable with the original's.
		</p>
	{/if}
	{#if builder}
		<StudyBuilder {builder} onlaunched={(id) => goto(`/studies/${id}?tab=operations`)} />
	{:else if !error}
		<LoadingState label="opening the builder" />
	{/if}
</div>
