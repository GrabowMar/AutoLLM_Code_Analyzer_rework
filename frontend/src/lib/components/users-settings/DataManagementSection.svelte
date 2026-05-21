<script lang="ts">
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { toast } from 'svelte-sonner';
	import Database from '@lucide/svelte/icons/database';
	import Download from '@lucide/svelte/icons/download';
	import Upload from '@lucide/svelte/icons/upload';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import AlertTriangle from '@lucide/svelte/icons/triangle-alert';

	const prefs = getPreferences();

	let showImportSection = $state(false);
	let importJson = $state('');
	let showResetConfirm = $state(false);

	function handleExport() {
		const json = prefs.exportPreferences();
		const blob = new Blob([json], { type: 'application/json' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = 'llm-lab-settings.json';
		a.click();
		URL.revokeObjectURL(url);
		toast.success('Settings exported');
	}

	function handleImport() {
		if (!importJson.trim()) {
			toast.error('Please paste JSON settings');
			return;
		}
		const ok = prefs.importPreferences(importJson);
		if (ok) {
			toast.success('Settings imported successfully');
			importJson = '';
			showImportSection = false;
		} else {
			toast.error('Invalid settings JSON');
		}
	}

	function handleReset() {
		prefs.resetPreferences();
		showResetConfirm = false;
		toast.success('Settings reset to defaults');
	}
</script>

<Card.Root>
	<Card.Header>
		<Card.Title class="flex items-center gap-2">
			<Database class="h-4 w-4 text-muted-foreground" />
			Data Management
		</Card.Title>
		<Card.Description>Export, import, or reset your local preferences.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		<div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
			<Button variant="outline" size="sm" class="gap-1.5" onclick={handleExport}>
				<Download class="h-3.5 w-3.5" />
				Export settings
			</Button>
			<Button variant="outline" size="sm" class="gap-1.5" onclick={() => (showImportSection = !showImportSection)}>
				<Upload class="h-3.5 w-3.5" />
				{showImportSection ? 'Hide import' : 'Import settings'}
			</Button>
			{#if !showResetConfirm}
				<Button variant="destructive" size="sm" class="gap-1.5 sm:ml-auto" onclick={() => (showResetConfirm = true)}>
					<RotateCcw class="h-3.5 w-3.5" />
					Reset to defaults
				</Button>
			{/if}
		</div>

		{#if showImportSection}
			<div class="space-y-2 rounded-md border border-border bg-muted/30 p-3">
				<Label for="import-json" class="text-[11px] uppercase tracking-wider text-muted-foreground" style="font-family: var(--font-mono);">
					Paste settings JSON
				</Label>
				<textarea
					id="import-json"
					class="flex min-h-[110px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
					style="font-family: var(--font-mono);"
					placeholder={'{"theme":"dark","accentColor":"purple", ...}'}
					bind:value={importJson}
				></textarea>
				<div class="flex gap-2">
					<Button size="sm" onclick={handleImport}>Confirm import</Button>
					<Button
						size="sm"
						variant="ghost"
						onclick={() => {
							showImportSection = false;
							importJson = '';
						}}
					>
						Cancel
					</Button>
				</div>
			</div>
		{/if}

		{#if showResetConfirm}
			<Alert variant="destructive">
				<AlertTriangle class="h-4 w-4" />
				<AlertDescription>
					<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
						<span>Are you sure? All local preferences will return to defaults.</span>
						<div class="flex gap-2 sm:ml-4">
							<Button size="sm" variant="destructive" onclick={handleReset}>Reset</Button>
							<Button size="sm" variant="ghost" onclick={() => (showResetConfirm = false)}>Cancel</Button>
						</div>
					</div>
				</AlertDescription>
			</Alert>
		{/if}
	</Card.Content>
</Card.Root>
