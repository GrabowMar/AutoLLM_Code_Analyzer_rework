<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { createAppTemplate, deleteAppTemplate, updateAppTemplate, type AppRequirementTemplate } from '$lib/api/generation';
	import Plus from '@lucide/svelte/icons/plus';
	import Search from '@lucide/svelte/icons/search';
	import LibraryPane from './LibraryPane.svelte';
	import SpecList from './SpecList.svelte';
	import SpecEditor from './SpecEditor.svelte';
	import { canEditSpec, type SpecPayload } from './spec';
	import type { AskConfirm, LibraryData } from './library.svelte';

	let { data, askConfirm }: { data: LibraryData; askConfirm: AskConfirm } = $props();

	let search = $state('');
	let category = $state('');
	/** `spec` null = new; `copy` duplicates `spec` under a new slug. */
	let current = $state<{ spec: AppRequirementTemplate | null; copy: boolean } | null>(null);
	let saving = $state(false);
	let error = $state('');

	const categories = $derived([...new Set(data.specs.items.map((s) => s.category).filter(Boolean))].sort());
	const shown = $derived.by(() => {
		const q = search.trim().toLowerCase();
		return data.specs.items.filter(
			(s) =>
				(!category || s.category === category) &&
				(!q || s.name.toLowerCase().includes(q) || s.slug.includes(q) || s.description.toLowerCase().includes(q))
		);
	});

	function open(spec: AppRequirementTemplate | null, copy = false) {
		error = '';
		current = { spec, copy };
	}

	function close() {
		current = null;
		error = '';
	}

	async function save(payload: SpecPayload, isNew: boolean) {
		saving = true;
		error = '';
		try {
			const saved = isNew ? await createAppTemplate(payload) : await updateAppTemplate(current!.spec!.slug, payload);
			toast.success(isNew ? `Created ${saved.name}` : `Saved ${saved.name}`);
			close();
			await data.specsChanged();
		} catch (e) {
			error = formatApiError(e, 'Save failed.');
		}
		saving = false;
	}

	function remove(s: AppRequirementTemplate) {
		askConfirm({
			title: `Delete app spec ${s.name}?`,
			description: 'The spec is removed from the Library. Jobs that already ran against it keep their snapshot.',
			confirmLabel: 'Delete app spec',
			action: async () => {
				await deleteAppTemplate(s.slug);
				if (current?.spec?.slug === s.slug) close();
				toast.success(`Deleted ${s.name}`);
				await data.specsChanged();
			},
		});
	}

	const readonly = $derived(!!current?.spec && !current.copy && !canEditSpec(current.spec, data.isStaff));
</script>

<LibraryPane
	title="App specs"
	description="What to build: requirements, a data model and the API contract. Agent runs on a spec are graded against its endpoints by a hidden conformance suite."
	usedIn="Sample Generator (Agent and Template modes) and Experiments"
	editing={current !== null}
	onCloseEditor={close}
>
	{#snippet toolbar()}
		<div class="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
			<div class="relative w-full sm:w-64">
				<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
				<Input bind:value={search} placeholder="Search app specs…" aria-label="Search app specs" class="h-9 pl-9" />
			</div>
			{#if categories.length > 1}
				<Select.Simple
					bind:value={category}
					items={categories.map((c) => ({ value: c, label: c }))}
					placeholder="All categories"
					clearLabel="All categories"
					aria-label="Filter by category"
					class="w-full sm:w-44"
				/>
			{/if}
		</div>
		<Button size="sm" class="w-full sm:w-auto" onclick={() => open(null)}><Plus /> New app spec</Button>
	{/snippet}

	{#snippet list()}
		{#if data.specs.error}
			<ErrorAlert message={data.specs.error} onretry={() => data.specs.load()} />
		{:else}
			<SpecList
				specs={shown}
				loading={data.specs.loading}
				activeSlug={current?.copy ? null : (current?.spec?.slug ?? null)}
				canEditCatalog={data.isStaff}
				filtered={!!search.trim() || !!category}
				onOpen={(s) => open(s)}
				onDuplicate={(s) => open(s, true)}
				onDelete={remove}
			/>
		{/if}
	{/snippet}

	{#snippet editor()}
		{#if current}
			{#key `${current.spec?.id ?? 'new'}:${current.copy}`}
				<SpecEditor
					initial={current.spec}
					copy={current.copy}
					{readonly}
					{categories}
					takenSlugs={data.specs.items.map((s) => s.slug)}
					{saving}
					{error}
					onSave={save}
					onDuplicate={() => current?.spec && open(current.spec, true)}
					onCancel={close}
				/>
			{/key}
		{/if}
	{/snippet}
</LibraryPane>
