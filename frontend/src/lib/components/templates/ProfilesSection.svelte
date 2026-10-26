<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		archiveGenerationProfile,
		createGenerationProfile,
		updateGenerationProfile,
		type GenerationProfile,
		type ProfileWritePayload,
	} from '$lib/api/generation';
	import Plus from '@lucide/svelte/icons/plus';
	import LibraryPane from './LibraryPane.svelte';
	import ProfileList from './ProfileList.svelte';
	import ProfileEditor from './ProfileEditor.svelte';
	import type { AskConfirm, LibraryData } from './library.svelte';

	let { data, askConfirm }: { data: LibraryData; askConfirm: AskConfirm } = $props();

	/** `profile` null = new; `duplicate` copies it into a new slug. */
	let current = $state<{ profile: GenerationProfile | null; duplicate: boolean } | null>(null);
	let saving = $state(false);
	let error = $state('');

	function open(profile: GenerationProfile | null, duplicate = false) {
		error = '';
		current = { profile, duplicate };
	}

	function close() {
		current = null;
		error = '';
	}

	async function save(payload: ProfileWritePayload, isNewSlug: boolean) {
		saving = true;
		error = '';
		try {
			if (isNewSlug) await createGenerationProfile(payload);
			else await updateGenerationProfile(payload.slug, payload);
			toast.success(`Saved ${payload.name}`);
			close();
			await data.profiles.load();
		} catch (e) {
			error = formatApiError(e, 'Save failed.');
		}
		saving = false;
	}

	function archive(p: GenerationProfile) {
		askConfirm({
			title: `Archive ${p.name}?`,
			description: 'Every version is archived. Existing jobs keep their frozen snapshots.',
			confirmLabel: 'Archive profile',
			action: async () => {
				await archiveGenerationProfile(p.slug);
				if (current?.profile?.slug === p.slug) close();
				await data.profiles.load();
			},
		});
	}
</script>

<LibraryPane
	title="Prompt profiles"
	description="Template mode only: ordered prompt blocks for one stack plus LLM defaults, rendered into the backend and frontend prompts of a one-pass scaffolding run. Agent runs get a fixed brief instead."
	usedIn="Sample Generator (Template mode)"
	editing={current !== null}
	onCloseEditor={close}
>
	{#snippet toolbar()}
		<span></span>
		<Button size="sm" class="w-full sm:w-auto" onclick={() => open(null)}><Plus /> New profile</Button>
	{/snippet}

	{#snippet list()}
		{#if data.profiles.error}
			<ErrorAlert message={data.profiles.error} onretry={() => data.profiles.load()} />
		{:else}
			<ProfileList
				profiles={data.profiles.items}
				loading={data.profiles.loading}
				activeSlug={current?.profile?.slug ?? null}
				canEditSystem={data.isStaff}
				onEdit={(p) => open(p)}
				onDuplicate={(p) => open(p, true)}
				onArchive={archive}
			/>
		{/if}
	{/snippet}

	{#snippet editor()}
		{#if current}
			{#key `${current.profile?.id ?? 'new'}:${current.duplicate}`}
				<ProfileEditor
					initial={current.profile}
					duplicate={current.duplicate}
					blocks={data.blocks.items}
					stacks={data.stacks.items}
					defaultStackSlug={data.agentConfig?.default_stack_slug}
					appTemplates={data.specs.items}
					{saving}
					{error}
					onSave={save}
					onCancel={close}
				/>
			{/key}
		{/if}
	{/snippet}
</LibraryPane>
