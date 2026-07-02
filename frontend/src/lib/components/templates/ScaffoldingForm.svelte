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

	export interface ScaffoldingFormData {
		name: string;
		slug: string;
		description: string;
		tech_stack_json: string;
		substitution_vars_csv: string;
	}

	interface Props {
		form: ScaffoldingFormData;
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
				{isEdit ? 'Edit' : 'New'} Scaffolding Template
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
				<Input bind:value={form.name} placeholder="e.g. React + FastAPI" class="h-9 text-xs transition-all hover:border-primary/45 focus-visible:ring-primary/20"
					oninput={() => { if (!isEdit) form.slug = slugify(form.name); }} />
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Slug</Label>
				<Input bind:value={form.slug} placeholder="e.g. react-fastapi" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Description</Label>
				<Textarea bind:value={form.description} rows={2} placeholder="Explain this stack setup and its intent..." class="text-xs leading-relaxed" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Tech Stack (JSON configuration)</Label>
				<Textarea bind:value={form.tech_stack_json} rows={5} placeholder={'{\n  "frontend": "React",\n  "backend": "FastAPI",\n  "database": "PostgreSQL"\n}'} class="font-mono text-xs leading-relaxed bg-muted/20" />
			</div>
			<div class="space-y-1.5 sm:col-span-2">
				<Label class="text-xs uppercase tracking-wider text-muted-foreground">Substitution Variables (comma-separated)</Label>
				<Input bind:value={form.substitution_vars_csv} placeholder="e.g. APP_NAME, ADMIN_EMAIL, PORT" class="h-9 text-xs font-mono transition-all hover:border-primary/45 focus-visible:ring-primary/20" />
			</div>
		</div>

		{#if error}
			<div class="rounded-md bg-red-500/10 border border-red-500/30 p-3 text-xs text-red-400 font-medium">
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
