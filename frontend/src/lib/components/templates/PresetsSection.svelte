<script lang="ts">
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { archiveRunPreset, createRunPreset, updateRunPreset, type PresetKind, type RunPreset } from '$lib/api/generation';
	import Plus from '@lucide/svelte/icons/plus';
	import LibraryPane from './LibraryPane.svelte';
	import PresetList from './PresetList.svelte';
	import PresetEditor, { type PresetSaveData } from './PresetEditor.svelte';
	import { PRESET_KIND_TITLES } from './presets';
	import type { AskConfirm, LibraryData } from './library.svelte';

	let { data, kind, askConfirm }: { data: LibraryData; kind: PresetKind; askConfirm: AskConfirm } = $props();

	let current = $state<{ preset: RunPreset | null; duplicate: boolean; harness: string } | null>(null);
	let saving = $state(false);
	let error = $state('');

	// A kind switch (nav) closes the editor of the previous kind.
	let shownKind = $state(untrack(() => kind));
	$effect.pre(() => {
		if (kind !== shownKind) {
			shownKind = kind;
			close();
		}
	});

	const INTRO: Record<PresetKind, string> = {
		harness: 'Which agent harness runs, with which options. Pick several in one batch to compare harness configurations.',
		sampling: 'Model-agnostic sampling settings (temperature, reasoning effort, output cap, …) applied identically to every model in a batch.',
		budget: 'Per-run request budget, safety caps and network access. Batch-wide, so every condition gets the same budget.',
	};

	const kindPresets = $derived(data.presets.items.filter((p) => p.kind === kind && (data.showArchivedPresets || !p.is_archived)));
	const tunableHarnesses = $derived((data.agentConfig?.harnesses ?? []).filter((h) => h.option_schema.length > 0));
	const cannotCreate = $derived(kind === 'harness' && tunableHarnesses.length === 0);

	function open(preset: RunPreset | null, duplicate = false, harness = '') {
		error = '';
		current = { preset, duplicate, harness };
	}

	function close() {
		current = null;
		error = '';
	}

	async function save(payload: PresetSaveData, isNew: boolean) {
		saving = true;
		error = '';
		try {
			const saved = isNew
				? await createRunPreset({ ...payload })
				: await updateRunPreset(payload.kind, payload.slug, {
						name: payload.name,
						description: payload.description,
						config: payload.config,
					});
			toast.success(isNew ? `Created ${saved.name}` : `Saved ${saved.name} v${saved.version}`);
			close();
			await data.presetsChanged(payload.kind);
		} catch (e) {
			error = formatApiError(e, 'Save failed.');
		}
		saving = false;
	}

	function archive(p: RunPreset) {
		askConfirm({
			title: `Archive ${p.name}?`,
			description:
				'Every version is archived and disappears from preset pickers. Jobs, batches and experiments that used it keep their reference.',
			confirmLabel: 'Archive preset',
			action: async () => {
				await archiveRunPreset(p.kind, p.slug);
				if (current?.preset?.slug === p.slug) close();
				toast.success(`Archived ${p.name}`);
				await data.presetsChanged(p.kind);
			},
		});
	}
</script>

<LibraryPane
	title="{PRESET_KIND_TITLES[kind]} presets"
	description="{INTRO[kind]} Changing a preset's content saves a new version; every job records the exact version it ran with."
	usedIn="Sample Generator (Agent mode) and Experiments"
	editing={current !== null}
	onCloseEditor={close}
>
	{#snippet toolbar()}
		<label class="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
			<Checkbox checked={data.showArchivedPresets} onCheckedChange={(on) => data.setShowArchivedPresets(on)} />
			Show archived
		</label>
		<Button
			size="sm"
			class="w-full sm:w-auto"
			disabled={cannotCreate}
			title={cannotCreate ? 'No harness has tunable options' : undefined}
			onclick={() => open(null)}
		>
			<Plus /> New {kind} preset
		</Button>
	{/snippet}

	{#snippet list()}
		{#if data.agentConfigError && kind !== 'sampling'}
			<ErrorAlert message={data.agentConfigError} onretry={() => data.loadAgentConfig()} />
		{/if}
		{#if data.presets.error}
			<ErrorAlert message={data.presets.error} onretry={() => data.presets.load()} />
		{:else}
			<PresetList
				{kind}
				presets={kindPresets}
				harnesses={data.agentConfig?.harnesses ?? []}
				loading={data.presets.loading || (kind === 'harness' && data.agentConfigLoading)}
				activeSlug={current?.preset?.slug ?? null}
				onCreate={(harness) => open(null, false, harness ?? '')}
				onEdit={(p) => open(p)}
				onDuplicate={(p) => open(p, true)}
				onArchive={archive}
			/>
		{/if}
	{/snippet}

	{#snippet editor()}
		{#if current}
			{#key `${kind}:${current.preset?.id ?? 'new'}:${current.duplicate}:${current.harness}`}
				<PresetEditor
					{kind}
					initial={current.preset}
					duplicate={current.duplicate}
					harnessName={current.harness}
					agentConfig={data.agentConfig}
					takenSlugs={data.presets.items.filter((p) => p.kind === kind).map((p) => p.slug)}
					{saving}
					{error}
					onSave={save}
					onCancel={close}
				/>
			{/key}
		{/if}
	{/snippet}
</LibraryPane>
