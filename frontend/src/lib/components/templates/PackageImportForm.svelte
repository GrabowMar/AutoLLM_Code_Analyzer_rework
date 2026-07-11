<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import XIcon from '@lucide/svelte/icons/x';

	interface Props {
		packageText: string;
		conflictStrategy: 'rename' | 'overwrite' | 'error';
		importing: boolean;
		error: string;
		onSubmit: () => void;
		onCancel: () => void;
	}
	let {
		packageText = $bindable(),
		conflictStrategy = $bindable(),
		importing,
		error,
		onSubmit,
		onCancel,
	}: Props = $props();

	async function readFile(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		const file = input.files?.[0];
		if (!file) return;
		packageText = await file.text();
		input.value = '';
	}
</script>

<Card.Root class="shadow-lg border-primary/20">
	<Card.Header class="pb-3 bg-muted/10 border-b">
		<div class="flex items-center justify-between">
			<Card.Title class="text-sm font-bold text-foreground">Import Template Package</Card.Title>
			<button type="button" class="text-muted-foreground hover:text-foreground cursor-pointer" onclick={onCancel}>
				<XIcon class="h-4 w-4" />
			</button>
		</div>
	</Card.Header>
	<Card.Content class="pt-5 space-y-4">
		<div class="space-y-1.5">
			<Label class="text-xs uppercase tracking-wider text-muted-foreground">Conflict Strategy</Label>
			<select bind:value={conflictStrategy} class="flex h-9 w-full rounded-md border border-input bg-background px-3 text-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 cursor-pointer">
				<option value="rename">Rename conflicts</option>
				<option value="overwrite">Overwrite my existing bundle</option>
				<option value="error">Fail on conflicts</option>
			</select>
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs uppercase tracking-wider text-muted-foreground">Package File</Label>
			<input
				type="file"
				accept=".json,.yaml,.yml,text/*"
				class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all file:border-0 file:bg-transparent file:text-sm file:font-medium file:text-foreground hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 cursor-pointer"
				onchange={readFile}
			/>
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs uppercase tracking-wider text-muted-foreground">Package Text (JSON or YAML)</Label>
			<Textarea bind:value={packageText} rows={14} placeholder="Paste a template package here…" class="font-mono text-[11px] leading-relaxed bg-muted/20" />
		</div>

		{#if error}
			<div class="rounded-md bg-destructive/10 border border-destructive/30 p-3 text-xs text-destructive font-medium">
				{error}
			</div>
		{/if}

		<div class="flex gap-2 pt-2 border-t">
			<Button size="sm" class="text-xs cursor-pointer shadow-xs" onclick={onSubmit} disabled={importing || !packageText.trim()}>
				{#if importing}
					<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Importing…
				{:else}
					<Save class="mr-1.5 h-3.5 w-3.5" /> Import Package
				{/if}
			</Button>
			<Button variant="outline" size="sm" class="text-xs cursor-pointer" onclick={onCancel}>
				Cancel
			</Button>
		</div>
	</Card.Content>
</Card.Root>
