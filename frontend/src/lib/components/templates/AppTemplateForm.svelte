<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import XIcon from '@lucide/svelte/icons/x';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { slugify } from './slugify';

	export interface AppTemplateFormData {
		name: string;
		slug: string;
		description: string;
		backend_requirements: string;
		frontend_requirements: string;
		admin_requirements: string;
	}

	interface Props {
		form: AppTemplateFormData;
		isEdit: boolean;
		saving: boolean;
		error: string;
		onSave: () => void;
		onCancel: () => void;
	}
	let { form = $bindable(), isEdit, saving, error, onSave, onCancel }: Props = $props();
</script>

<Card.Root>
	<Card.Header class="border-b">
		<div class="flex items-start justify-between gap-2">
			<div class="space-y-1">
				<Card.Title>{isEdit ? 'Edit' : 'New'} app spec</Card.Title>
				<Card.Description>Requirements are one per line.</Card.Description>
			</div>
			<Button variant="ghost" size="icon" class="size-8" aria-label="Close editor" onclick={onCancel}>
				<XIcon />
			</Button>
		</div>
	</Card.Header>
	<Card.Content class="space-y-4">
		<div class="grid gap-4 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label for="app-name" class="text-xs">Name</Label>
				<Input id="app-name" bind:value={form.name} placeholder="e.g. Chat Messenger" class="h-9 text-xs"
					oninput={() => { if (!isEdit) form.slug = slugify(form.name); }} />
			</div>
			<div class="space-y-1.5">
				<Label for="app-slug" class="text-xs">Slug</Label>
				<Input id="app-slug" bind:value={form.slug} placeholder="e.g. chat_messenger" class="h-9 text-xs font-mono" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label for="app-desc" class="text-xs">Description</Label>
				<Textarea id="app-desc" bind:value={form.description} rows={2} placeholder="Brief summary of what this application does..." class="text-xs leading-relaxed" />
			</div>
			<div class="space-y-1.5">
				<Label for="app-backend" class="text-xs">Backend Requirements</Label>
				<Textarea id="app-backend" bind:value={form.backend_requirements} rows={6} placeholder={"e.g.\nWebSockets for active connections\nCRUD endpoints for messages\nUser identity session management"} class="font-mono text-[11px] leading-normal bg-muted/20" />
			</div>
			<div class="space-y-1.5">
				<Label for="app-frontend" class="text-xs">Frontend Requirements</Label>
				<Textarea id="app-frontend" bind:value={form.frontend_requirements} rows={6} placeholder={"e.g.\nReal-time scrolling chat view\nSidebar of direct message lists\nVisual status rings for users"} class="font-mono text-[11px] leading-normal bg-muted/20" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label for="app-admin" class="text-xs">Admin Features</Label>
				<Textarea id="app-admin" bind:value={form.admin_requirements} rows={3} placeholder={"e.g.\nGlobal analytics tracking dashboard\nManage and freeze user rooms"} class="font-mono text-[11px] leading-normal bg-muted/20" />
			</div>
		</div>

		{#if error}
			<ErrorAlert message={error} />
		{/if}

		<div class="flex justify-end gap-2 pt-4 border-t">
			<Button variant="outline" size="sm" onclick={onCancel}>Cancel</Button>
			<Button size="sm" onclick={onSave} disabled={saving || !form.name || !form.slug}>
				{#if saving}
					<LoaderCircle class="animate-spin" /> Saving…
				{:else}
					<Save /> Save app spec
				{/if}
			</Button>
		</div>
	</Card.Content>
</Card.Root>
