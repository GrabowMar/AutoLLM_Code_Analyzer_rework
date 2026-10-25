<script lang="ts">
	// New experiment (or edit a draft): resolves where the design starts from —
	// `?edit=<id>` a draft, `?from=<id>` a clone, `?from_batch=<uuid>` a batch's
	// grid, `?models=` preselected models, else the session draft — then hands
	// it to the single-page composer.
	import { untrack } from 'svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { Button } from '$lib/components/ui/button';
	import { getExperiment } from '$lib/api/experiments';
	import { getGenerationBatch } from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import ExperimentComposer from '$lib/components/experiments/ExperimentComposer.svelte';
	import {
		clearComposerDraft,
		defaultComposerDraft,
		draftFromExperiment,
		loadComposerDraft,
		type ExperimentDraft,
	} from '$lib/components/experiments/composer';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let initial = $state<ExperimentDraft | null>(null);
	let source = $state('');
	let error = $state('');
	let version = $state(0);

	const editing = $derived(!!data.editId);

	async function resolve() {
		error = '';
		initial = null;
		try {
			if (data.editId) {
				const exp = await getExperiment(data.editId);
				if (exp.status !== 'draft') {
					error = 'Only drafts can be edited. Launched experiments can change priority and limits on their Design tab.';
					return;
				}
				initial = draftFromExperiment(exp, { clone: false });
				source = `Editing draft “${exp.name}”`;
				return;
			}
			if (data.fromId) {
				const exp = await getExperiment(data.fromId);
				initial = draftFromExperiment(exp, { clone: true });
				source = `Clone of “${exp.name}”: same design, current model digests, a new seed.`;
				return;
			}
			if (data.fromBatchId) {
				const batch = await getGenerationBatch(data.fromBatchId);
				const req = batch.request ?? {};
				const base = defaultComposerDraft();
				initial = {
					...base,
					name: `Experiment from ${batch.name}`,
					mode: batch.mode === 'scaffolding' ? 'scaffolding' : 'agent',
					stackSlug: req.stack_slug ?? batch.stack_slug ?? '',
					modelIds: req.model_ids ?? [],
					harnessPresetIds: req.harness_preset_ids ?? [],
					profileIds: req.profile_id != null ? [req.profile_id] : [],
					appIds: req.app_requirement_ids ?? [],
					repeats: req.trials ?? base.repeats,
					samplingPresetId: req.sampling_preset_id ?? null,
					budgetPresetId: req.budget_preset_id ?? null,
				};
				source = `From batch “${batch.name}”.`;
				return;
			}
			const stored = loadComposerDraft();
			const base = stored ?? defaultComposerDraft();
			if (data.modelIds.length > 0) {
				initial = { ...base, modelIds: [...new Set([...base.modelIds, ...data.modelIds])] };
				source = `${data.modelIds.length} model${data.modelIds.length === 1 ? '' : 's'} preselected from the endpoint.`;
			} else {
				initial = base;
				source = stored ? 'Continuing your unsaved draft.' : '';
			}
		} catch (e) {
			error = formatApiError(e, 'Could not load the starting point.');
		}
	}

	$effect(() => {
		void data.editId;
		void data.fromId;
		void data.fromBatchId;
		void data.modelIds;
		untrack(() => void resolve());
	});

	function startOver() {
		clearComposerDraft();
		initial = defaultComposerDraft();
		source = '';
		version += 1;
	}
</script>

<div class="space-y-6">
	<PageHeader
		title={editing ? 'Edit experiment' : 'New experiment'}
		description="Models × harness presets (or prompt profiles) × apps × repeats, with shared seeds and a pre-registered baseline."
		back={{ href: editing && data.editId ? `/experiments/${data.editId}` : '/experiments', label: editing ? 'Experiment' : 'Experiments' }}
	>
		{#snippet actions()}
			{#if !editing && initial && source === 'Continuing your unsaved draft.'}
				<Button variant="outline" size="sm" onclick={startOver}><RotateCcw />Start over</Button>
			{/if}
		{/snippet}
		{#if source}
			<p class="text-xs text-muted-foreground">{source}</p>
		{/if}
	</PageHeader>

	{#if error}
		<ErrorAlert message={error} onretry={resolve} />
	{:else if !initial}
		<LoadingState label="preparing the composer" />
	{:else}
		{#key version}
			<ExperimentComposer mode={editing ? 'edit' : 'create'} {initial} experimentId={data.editId ?? undefined} />
		{/key}
	{/if}
</div>
