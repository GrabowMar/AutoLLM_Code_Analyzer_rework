<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { archiveStack, createStack, updateStack, type Stack, type StackWritePayload } from '$lib/api/generation';
	import Plus from '@lucide/svelte/icons/plus';
	import Search from '@lucide/svelte/icons/search';
	import LibraryPane from './LibraryPane.svelte';
	import StacksList from './StacksList.svelte';
	import StackEditor from './StackEditor.svelte';
	import type { AskConfirm, LibraryData } from './library.svelte';

	let { data, askConfirm }: { data: LibraryData; askConfirm: AskConfirm } = $props();

	let search = $state('');
	/** `slug` null = new; `duplicate` copies `slug` into a new stack. */
	let current = $state<{ slug: string | null; duplicate: boolean } | null>(null);
	let saving = $state(false);
	let error = $state('');

	const shown = $derived.by(() => {
		const q = search.trim().toLowerCase();
		return data.stacks.items.filter(
			(s) => !q || s.slug.includes(q) || (s.name ?? '').toLowerCase().includes(q) || (s.aliases ?? []).some((a) => a.includes(q))
		);
	});

	function close() {
		current = null;
		error = '';
	}

	async function save(payload: StackWritePayload, isNewSlug: boolean) {
		saving = true;
		error = '';
		try {
			if (isNewSlug) await createStack(payload);
			else await updateStack(current!.slug!, payload);
			toast.success(`Saved stack ${payload.slug}`);
			close();
			await data.stacks.load();
		} catch (e) {
			error = formatApiError(e, 'Save failed.');
		}
		saving = false;
	}

	function archive(s: Stack) {
		askConfirm({
			title: `Archive stack ${s.slug}?`,
			description: 'Every version is archived. Existing jobs keep their pinned snapshots; new runs can no longer pick it.',
			confirmLabel: 'Archive stack',
			action: async () => {
				await archiveStack(s.slug);
				if (current?.slug === s.slug) close();
				await data.stacks.load();
			},
		});
	}
</script>

<LibraryPane
	title="Stacks"
	description="Where it runs: the project skeleton a run starts from, with its Dockerfile, port and entry point. Stacks you create need an admin's approval before anyone else can run them."
	usedIn="Sample Generator (every mode that builds an app) and Experiments"
	editing={current !== null}
	onCloseEditor={close}
>
	{#snippet toolbar()}
		<div class="relative w-full sm:w-64">
			<Search class="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" aria-hidden="true" />
			<Input bind:value={search} placeholder="Search stacks…" aria-label="Search stacks" class="h-9 pl-9" />
		</div>
		<Button size="sm" class="w-full sm:w-auto" onclick={() => { error = ''; current = { slug: null, duplicate: false }; }}>
			<Plus /> New stack
		</Button>
	{/snippet}

	{#snippet list()}
		{#if data.stacks.error}
			<ErrorAlert message={data.stacks.error} onretry={() => data.stacks.load()} />
		{:else}
			<StacksList
				stacks={shown}
				loading={data.stacks.loading}
				activeSlug={current?.slug ?? null}
				canModerate={data.isStaff}
				onEdit={(s) => { error = ''; current = { slug: s.slug, duplicate: false }; }}
				onDuplicate={(s) => { error = ''; current = { slug: s.slug, duplicate: true }; }}
				onArchive={archive}
			/>
		{/if}
	{/snippet}

	{#snippet editor()}
		{#if current}
			{#key `${current.slug ?? 'new'}:${current.duplicate}`}
				<StackEditor
					editSlug={current.duplicate ? null : current.slug}
					duplicateFromSlug={current.duplicate ? current.slug : null}
					{saving}
					{error}
					onSave={save}
					onCancel={close}
				/>
			{/key}
		{/if}
	{/snippet}
</LibraryPane>
