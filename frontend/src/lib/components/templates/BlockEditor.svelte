<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { untrack } from 'svelte';
	import { slugify } from '$lib/components/templates/slugify';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { ContentBlock } from '$lib/api/client';

	export interface BlockSavePayload {
		mode: 'create' | 'new-version';
		slug: string;
		block_type: string;
		name: string;
		description: string;
		content: string;
		metadata: Record<string, unknown>;
	}

	interface Props {
		/** Existing block → "new version" mode; null → create a fresh block. */
		initial: ContentBlock | null;
		saving: boolean;
		error: string;
		onSave: (payload: BlockSavePayload) => void;
		onCancel: () => void;
	}
	let { initial, saving, error, onSave, onCancel }: Props = $props();

	// Snapshot the prop once — the parent remounts this editor via {#key}
	// whenever a different block is opened.
	const seed = untrack(() => initial);

	const blockTypeOptions = [
		'prompt_stage',
		'prompt_tone',
		'prompt_rules',
		'scaffold_hint',
		'requirement',
		'api_schema',
		'validation',
		'eval_rubric',
	];

	let name = $state(seed?.name ?? '');
	let slug = $state(seed?.slug ?? '');
	let slugTouched = $state(false);
	let blockType = $state(seed?.block_type ?? 'prompt_tone');
	let description = $state(seed?.description ?? '');
	let content = $state(seed?.content ?? '');
	let stage = $state((seed?.metadata?.stage as string) ?? 'backend');
	let role = $state((seed?.metadata?.role as string) ?? 'system');

	$effect(() => {
		if (!initial && !slugTouched) slug = slugify(name);
	});

	function submit() {
		const metadata: Record<string, unknown> =
			blockType === 'prompt_stage' ? { stage, role } : { ...(initial?.metadata ?? {}) };
		onSave({
			mode: initial ? 'new-version' : 'create',
			slug,
			block_type: blockType,
			name,
			description,
			content,
			metadata,
		});
	}
</script>

<Card.Root>
	<Card.Header class="pb-3">
		<Card.Title class="text-base">
			{#if initial}
				New version of <span class="font-mono">{initial.slug}</span>
				<span class="text-xs font-normal text-muted-foreground">v{initial.version} → next — profiles keep their pinned version until re-pointed</span>
			{:else}
				New Block
			{/if}
		</Card.Title>
	</Card.Header>
	<Card.Content class="space-y-4">
		<div class="grid gap-3 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label class="text-xs" for="block-name">Name</Label>
				<Input id="block-name" bind:value={name} class="h-9 text-sm" />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs" for="block-slug">Slug</Label>
				<Input id="block-slug" bind:value={slug} disabled={initial !== null} oninput={() => (slugTouched = true)} class="h-9 text-sm font-mono" />
			</div>
		</div>
		<div class="grid gap-3 sm:grid-cols-3">
			<div class="space-y-1.5">
				<Label class="text-xs" for="block-type">Type</Label>
				<select
					id="block-type"
					bind:value={blockType}
					disabled={initial !== null}
					class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs disabled:opacity-60"
				>
					{#each blockTypeOptions as t}<option value={t}>{t}</option>{/each}
				</select>
			</div>
			{#if blockType === 'prompt_stage'}
				<div class="space-y-1.5">
					<Label class="text-xs" for="block-stage">Stage</Label>
					<select id="block-stage" bind:value={stage} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs">
						<option value="backend">backend</option>
						<option value="frontend">frontend</option>
					</select>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="block-role">Role</Label>
					<select id="block-role" bind:value={role} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs">
						<option value="system">system</option>
						<option value="user">user</option>
					</select>
				</div>
			{/if}
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs" for="block-description">Description</Label>
			<Input id="block-description" bind:value={description} class="h-9 text-sm" />
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs" for="block-content">Content (Jinja2)</Label>
			<Textarea id="block-content" bind:value={content} rows={14} class="text-xs font-mono leading-relaxed" />
			<p class="text-[10px] text-muted-foreground">
				Variables: <span class="font-mono">name, slug, description, backend_requirements, frontend_requirements, admin_requirements, api_endpoints, data_model</span>{#if blockType === 'prompt_stage' && stage === 'frontend'}, <span class="font-mono">backend_api_context</span>{/if}
			</p>
		</div>

		{#if error}
			<div class="rounded-md bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">{error}</div>
		{/if}

		<div class="flex items-center justify-end gap-2 border-t pt-4">
			<Button variant="outline" size="sm" onclick={onCancel} disabled={saving}>Cancel</Button>
			<Button size="sm" onclick={submit} disabled={saving || !name.trim() || !slug.trim() || !content.trim()}>
				{#if saving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{/if}
				{initial ? 'Create new version' : 'Create block'}
			</Button>
		</div>
	</Card.Content>
</Card.Root>
