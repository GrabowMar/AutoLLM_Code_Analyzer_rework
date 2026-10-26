<script lang="ts">
	import { importModelsFromJson, type ModelImportResult } from '$lib/api/client';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { toast } from 'svelte-sonner';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Upload from '@lucide/svelte/icons/upload';
	import FileJson from '@lucide/svelte/icons/file-json';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	let fileName = $state('');
	let selectedFile = $state<File | null>(null);
	let importing = $state(false);
	let error = $state('');
	let result = $state<ModelImportResult | null>(null);

	function handleFileChange(event: Event) {
		const target = event.target as HTMLInputElement;
		selectedFile = target.files?.[0] ?? null;
		fileName = selectedFile?.name ?? '';
		error = '';
		result = null;
	}

	async function handleImport() {
		if (!selectedFile) {
			error = 'Choose a JSON file to import.';
			return;
		}

		if (!selectedFile.name.toLowerCase().endsWith('.json')) {
			error = 'Only JSON model imports are supported in the new app right now.';
			return;
		}

		importing = true;
		error = '';
		result = null;

		try {
			const text = await selectedFile.text();
			const payload = JSON.parse(text) as unknown;
			result = await importModelsFromJson(payload);
			toast.success(`Imported ${result.imported} model records.`);
		} catch (caughtError) {
			error = caughtError instanceof Error ? caughtError.message : 'Import failed.';
			toast.error(error);
		} finally {
			importing = false;
		}
	}
</script>

<div class="space-y-4">
	<PageHeader
		title="Import Models"
		description="Import model definitions from this app's model JSON export (round-trips) or a raw OpenRouter model payload (.json)."
		back={{ href: '/models', label: 'Models' }}
	/>

	<div class="mx-auto max-w-lg">
		<Card.Root>
			<Card.Header>
				<div class="flex items-center gap-2">
					<Upload class="h-4 w-4 text-muted-foreground" />
					<Card.Title>Upload File</Card.Title>
				</div>
			</Card.Header>
			<Card.Content>
				<div class="space-y-4">
					<div class="flex flex-col items-center justify-center gap-3 rounded-md border-2 border-dashed p-4 transition-colors hover:border-primary/50">
						<FileJson class="h-6 w-6 text-muted-foreground" aria-hidden="true" />
						<div class="text-center">
							<p class="text-sm font-medium">
								{#if fileName}
									{fileName}
								{:else}
									Choose a JSON file to import
								{/if}
							</p>
						</div>
						<Input
							type="file"
							accept=".json,application/json"
							class="max-w-xs"
							onchange={handleFileChange}
							aria-label="Model JSON file"
						/>
					</div>
					{#if error}
						<ErrorAlert message={error} />
					{/if}
					{#if result}
						<div class="rounded-md border border-success/30 bg-success/10 p-3 text-sm text-success-fg" role="status">
							Imported {result.imported} of {result.count} records.
						</div>
					{/if}
					<Button class="w-full" onclick={handleImport} disabled={importing || !selectedFile}>
						{#if importing}
							<LoaderCircle class="mr-2 h-4 w-4 animate-spin" />
							Importing...
						{:else}
							<Upload class="mr-2 h-4 w-4" />
							Import Models
						{/if}
					</Button>
				</div>
			</Card.Content>
		</Card.Root>
	</div>
</div>
