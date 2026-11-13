<script lang="ts">
	// The Study builder (spec 10.2). `?draft=<id>` resumes a saved draft; `?clone=<id>[&clone_mode=latest_versions]`
	// starts from a copy (exact pins by default); `?mode=slot&slot_sets=a,b&targets=x,y` pre-fills a slot study
	// from the slot picker; `?model=<id>` or `?models=a,b` (endpoint page) pre-fills the model factor. The first save puts `?draft=<id>` in the URL, so a reload resumes the same draft.
	import { onDestroy, untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto, replaceState } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { formatApiError } from '$lib/api/core';
	import { cloneStudy, getStudy } from '$lib/api/v3/studies';
	import type { Design, StudyMode, StudyOut } from '$lib/api/v3/types';
	import { BuilderState, type BuilderMeta } from '$lib/components/studies/builder/builder.svelte';
	import { Button } from '$lib/components/ui/button';
	import StudyBuilder from '$lib/components/studies/builder/StudyBuilder.svelte';
	import { defaultDesign, setLevels } from '$lib/components/studies/builder/design';
	import { loadModelCatalog, modelLevelsFromRefs } from '$lib/components/studies/shared/catalog';
	import { isEditable } from '$lib/components/studies/format';
	import CloudAlert from '@lucide/svelte/icons/cloud-alert';
	import CloudCheck from '@lucide/svelte/icons/cloud-check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	let builder = $state<BuilderState | null>(null);
	let error = $state('');
	let versionDiffs = $state<{ factor: string; from: string; to: string }[]>([]);
	let unknownModels = $state<string[]>([]);
	// null until the first init: a bare `/experiments/new` has the key '' (empty search), which must still init.
	let started: string | null = null;

	const list = (key: string) =>
		(page.url.searchParams.get(key) ?? '')
			.split(',')
			.map((s) => s.trim())
			.filter(Boolean);

	// Before the first server save (it needs models, tasks and a stack) the design lives only here: keep it in
	// sessionStorage so a reload or a detour does not lose it. Cleared once the draft exists.
	const STASH = 'llmlab.experiment-builder.unsaved.v1';
	let restored = $state(false);
	function readStash(): { design: Design; meta: Partial<BuilderMeta> } | null {
		try {
			const raw = sessionStorage.getItem(STASH);
			const parsed = raw ? JSON.parse(raw) : null;
			return parsed?.design?.mode ? parsed : null;
		} catch {
			return null;
		}
	}
	function clearStash() {
		try {
			sessionStorage.removeItem(STASH);
		} catch {
			/* storage blocked */
		}
	}
	$effect(() => {
		const b = builder;
		if (!b) return;
		if (b.studyId) return clearStash();
		const snapshot = JSON.stringify({ design: b.design, meta: b.meta });
		try {
			sessionStorage.setItem(STASH, snapshot);
		} catch {
			/* storage blocked: nothing to restore later */
		}
	});
	function startOver() {
		clearStash();
		restored = false;
		builder?.destroy();
		builder = new BuilderState(defaultDesign('app'), {}, { onCreated });
		replaceState(new URL(page.url.pathname, page.url.origin), {});
	}

	function onCreated(study: StudyOut) {
		const url = new URL(page.url);
		url.search = `?draft=${study.id}`;
		url.hash = location.hash; // the open builder step (kept out of `page.url` by shallow routing)
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
		started = `draft:${study.id}`;
	}

	async function init(key: string) {
		started = key; // the clone branch and the first save move it on to `draft:<id>`
		error = '';
		unknownModels = [];
		builder?.destroy();
		builder = null;
		restored = false;
		const params = page.url.searchParams;
		try {
			const draftId = params.get('draft');
			const cloneId = params.get('clone');
			if (draftId) {
				const study = await getStudy(draftId);
				if (!isEditable(study.status, study.locked)) {
					await goto(`/experiments/${study.id}`, { replaceState: true });
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
				const stashed = refs.length || mode === 'slot' ? null : readStash();
				builder = stashed ? new BuilderState(stashed.design, stashed.meta, { onCreated }) : new BuilderState(design, {}, { onCreated });
				restored = !!stashed;
			}
		} catch (e) {
			error = formatApiError(e, 'Could not open the experiment.');
		}
	}

	// A fresh `/experiments/new` (or another draft) re-initialises; our own `?draft=` replacement does not.
	$effect(() => {
		const key = page.url.searchParams.get('draft') ? `draft:${page.url.searchParams.get('draft')}` : page.url.search;
		untrack(() => {
			if (key !== started) void init(key);
		});
	});

	onDestroy(() => builder?.destroy());

	const title = $derived(builder?.study ? `Design: ${builder.study.name}` : 'New experiment');

	// Autosave state for the header: the draft is saved server-side as the user edits.
	let now = $state(Date.now());
	$effect(() => {
		const t = setInterval(() => (now = Date.now()), 15_000);
		return () => clearInterval(t);
	});
	const saveState = $derived.by(() => {
		const b = builder;
		if (!b) return null;
		if (b.saving) return { tone: 'busy', text: 'Saving…' };
		if (b.saveError) return { tone: 'error', text: 'Not saved' };
		if (!b.studyId) return { tone: 'idle', text: 'Saved as a draft once models, tasks and a stack are chosen' };
		if (b.held) return { tone: 'idle', text: 'Edits wait for the required choices before saving' };
		if (b.pending || b.dirty) return { tone: 'busy', text: 'Unsaved edits…' };
		const at = b.lastSavedAt;
		if (!at) return { tone: 'ok', text: 'Draft saved' };
		const s = Math.max(0, Math.round((now - at) / 1000));
		return { tone: 'ok', text: s < 45 ? 'Draft saved just now' : `Draft saved ${Math.round(s / 60)} min ago` };
	});
</script>

<div class="space-y-4">
	<PageHeader
		{title}
		description="Choose models, tasks and what varies; check the plan and its cost; launch. Every edit is saved as a draft."
		back={{ href: '/experiments', label: 'Experiments' }}
	>
		{#snippet meta()}
			{#if builder?.study}<StatusBadge status={builder.study.status} />{/if}
		{/snippet}
		{#snippet actions()}
			{#if saveState}
				<span class="save-state" data-tone={saveState.tone} role="status">
					{#if saveState.tone === 'busy'}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />{:else if saveState.tone === 'ok'}<CloudCheck
							class="size-3.5"
							aria-hidden="true"
						/>{:else if saveState.tone === 'error'}<CloudAlert class="size-3.5" aria-hidden="true" />{/if}
					{saveState.text}
				</span>
			{/if}
		{/snippet}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => init(page.url.search)} />{/if}
	{#if restored && builder && !builder.studyId}
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-muted/40 px-3 py-2 text-xs" role="note">
			<span class="min-w-0 flex-1">Restored the design you started in this tab. It becomes a saved draft once models, tasks and a stack are chosen.</span>
			<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={startOver}>Start over</Button>
		</div>
	{/if}
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
		{#key builder}
			<StudyBuilder {builder} onlaunched={(id) => goto(`/experiments/${id}?tab=operations`)} />
		{/key}
	{:else if !error}
		<LoadingState label="opening the builder" />
	{/if}
</div>

<style>
	.save-state {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.save-state[data-tone='ok'] :global(svg) {
		color: var(--success-fg);
	}
	.save-state[data-tone='error'] {
		color: var(--danger-fg);
	}
</style>
