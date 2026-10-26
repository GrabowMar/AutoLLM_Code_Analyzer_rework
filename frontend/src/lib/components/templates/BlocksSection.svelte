<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { createBlockVersion, createContentBlock, type ContentBlock } from '$lib/api/generation';
	import Plus from '@lucide/svelte/icons/plus';
	import LibraryPane from './LibraryPane.svelte';
	import BlockList from './BlockList.svelte';
	import BlockEditor, { type BlockSavePayload } from './BlockEditor.svelte';
	import type { LibraryData } from './library.svelte';

	let { data }: { data: LibraryData } = $props();

	/** `block` null = new block. */
	let current = $state<{ block: ContentBlock | null } | null>(null);
	let saving = $state(false);
	let error = $state('');

	function close() {
		current = null;
		error = '';
	}

	async function save(payload: BlockSavePayload) {
		saving = true;
		error = '';
		try {
			if (payload.mode === 'new-version') {
				await createBlockVersion(payload.slug, {
					content: payload.content,
					name: payload.name,
					description: payload.description,
					metadata: payload.metadata,
				});
			} else {
				await createContentBlock({
					block_type: payload.block_type,
					slug: payload.slug,
					name: payload.name,
					description: payload.description,
					content: payload.content,
					metadata: payload.metadata,
				});
			}
			toast.success(`Saved block ${payload.slug}`);
			close();
			await data.blocks.load();
		} catch (e) {
			error = formatApiError(e, 'Save failed.');
		}
		saving = false;
	}
</script>

<LibraryPane
	title="Prompt blocks"
	description="Template mode only: versioned pieces of prompt text (stages, roles, tone, …) that profiles reference. Editing a block saves a new version; profiles keep the version they pin."
	usedIn="Prompt profiles"
	editing={current !== null}
	onCloseEditor={close}
>
	{#snippet toolbar()}
		<Button size="sm" class="w-full sm:w-auto" onclick={() => { error = ''; current = { block: null }; }}><Plus /> New block</Button>
	{/snippet}

	{#snippet list()}
		{#if data.blocks.error}
			<ErrorAlert message={data.blocks.error} onretry={() => data.blocks.load()} />
		{:else}
			<BlockList
				blocks={data.blocks.items}
				loading={data.blocks.loading}
				activeSlug={current?.block?.slug ?? null}
				onEdit={(b) => { error = ''; current = { block: b }; }}
			/>
		{/if}
	{/snippet}

	{#snippet editor()}
		{#if current}
			{#key current.block?.id ?? 'new'}
				<BlockEditor initial={current.block} {saving} {error} onSave={save} onCancel={close} />
			{/key}
		{/if}
	{/snippet}
</LibraryPane>
