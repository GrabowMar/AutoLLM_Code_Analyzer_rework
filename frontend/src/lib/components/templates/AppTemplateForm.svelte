<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import XIcon from '@lucide/svelte/icons/x';
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

<Card.Root class="shadow-lg border-primary/20">
	<Card.Header class="pb-3 bg-muted/10 border-b">
		<div class="flex items-center justify-between">
			<Card.Title class="text-sm font-bold text-foreground">
				{isEdit ? 'Edit' : 'New'} App Requirement Template
			</Card.Title>
			<button type="button" class="text-muted-foreground hover:text-foreground cursor-pointer" onclick={onCancel}>
				<XIcon class="h-4 w-4" />
			</button>
		</div>
	</Card.Header>
	<Card.Content class="pt-5 space-y-4">
		<div class="grid gap-4 sm:grid-cols-2">
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Name</Label>
				<Input bind:value={form.name} placeholder="e.g. Chat Messenger" class="h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20"
					oninput={() => { if (!isEdit) form.slug = slugify(form.name); }} />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Slug</Label>
				<Input bind:value={form.slug} placeholder="e.g. chat_messenger" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Description</Label>
				<Textarea bind:value={form.description} rows={2} placeholder="Brief summary of what this application does..." class="text-xs leading-relaxed" />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Backend Requirements (one per line)</Label>
				<Textarea bind:value={form.backend_requirements} rows={6} placeholder={"e.g.\nWebSockets for active connections\nCRUD endpoints for messages\nUser identity session management"} class="font-mono text-[11px] leading-normal bg-muted/20" />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Frontend Requirements (one per line)</Label>
				<Textarea bind:value={form.frontend_requirements} rows={6} placeholder={"e.g.\nReal-time scrolling chat view\nSidebar of direct message lists\nVisual status rings for users"} class="font-mono text-[11px] leading-normal bg-muted/20" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Admin Features (one per line)</Label>
				<Textarea bind:value={form.admin_requirements} rows={3} placeholder={"e.g.\nGlobal analytics tracking dashboard\nManage and freeze user rooms"} class="font-mono text-[11px] leading-normal bg-muted/20" />
			</div>
		</div>

		{#if error}
			<div class="rounded-md bg-destructive/10 border border-destructive/30 p-3 text-xs text-destructive font-medium">
				{error}
			</div>
		{/if}

		<div class="flex gap-2 pt-2 border-t">
			<Button size="sm" class="text-xs cursor-pointer shadow-xs" onclick={onSave} disabled={saving || !form.name || !form.slug}>
				{#if saving}
					<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Saving…
				{:else}
					<Save class="mr-1.5 h-3.5 w-3.5" /> Save Template
				{/if}
			</Button>
			<Button variant="outline" size="sm" class="text-xs cursor-pointer" onclick={onCancel}>
				Cancel
			</Button>
		</div>
	</Card.Content>
</Card.Root>
