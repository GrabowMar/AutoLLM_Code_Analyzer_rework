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

	export interface PromptFormData {
		name: string;
		slug: string;
		stage: string;
		role: string;
		content: string;
	}

	interface Props {
		form: PromptFormData;
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
				{isEdit ? 'Edit' : 'New'} Prompt Template
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
				<Input bind:value={form.name} placeholder="e.g. Frontend User Prompt" class="h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20"
					oninput={() => { if (!isEdit) form.slug = slugify(form.name); }} />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Slug</Label>
				<Input bind:value={form.slug} placeholder="e.g. frontend-user-v1" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Execution Stage</Label>
				<select bind:value={form.stage} class="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 cursor-pointer">
					<option value="backend">Backend generation</option>
					<option value="frontend">Frontend UI generation</option>
				</select>
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">LLM Chat Role</Label>
				<select bind:value={form.role} class="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 cursor-pointer">
					<option value="system">System prompt</option>
					<option value="user">User instruction</option>
				</select>
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground flex justify-between">
					<span>Prompt Body (Jinja2 template syntax)</span>
				</Label>
				<Textarea bind:value={form.content} rows={12} placeholder="Write instructions using Jinja2 blocks, e.g. You are a developer building {{ name }}..." class="font-mono text-[11px] leading-relaxed bg-muted/20" />
				<div class="p-3 bg-muted/40 rounded-md border text-[10px] text-muted-foreground leading-relaxed">
					<strong class="text-foreground">Jinja2 variables supplied by Generator:</strong> <code class="bg-muted px-1 rounded text-primary">name</code>, <code class="bg-muted px-1 rounded text-primary">description</code>, <code class="bg-muted px-1 rounded text-primary">backend_requirements</code>, <code class="bg-muted px-1 rounded text-primary">frontend_requirements</code>, <code class="bg-muted px-1 rounded text-primary">admin_requirements</code>, <code class="bg-muted px-1 rounded text-primary">api_endpoints</code>, <code class="bg-muted px-1 rounded text-primary">admin_api_endpoints</code>, <code class="bg-muted px-1 rounded text-primary">data_model</code>, <code class="bg-muted px-1 rounded text-primary">backend_api_context</code> (frontend stage only).
				</div>
			</div>
		</div>

		{#if error}
			<div class="rounded-md bg-destructive/10 border border-destructive/30 p-3 text-xs text-destructive font-medium">
				{error}
			</div>
		{/if}

		<div class="flex gap-2 pt-2 border-t">
			<Button size="sm" class="text-xs cursor-pointer shadow-xs" onclick={onSave} disabled={saving || !form.name || !form.slug || !form.content}>
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
