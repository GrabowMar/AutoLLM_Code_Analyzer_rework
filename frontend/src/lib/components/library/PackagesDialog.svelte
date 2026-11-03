<script lang="ts">
	/**
	 * PackagesDialog — library packages (YAML, package v4):
	 * - **Export**: the given refs (`kind:slug@v`), with their dependencies by default, as a download.
	 * - **Import**: a YAML file is first dry-run (what would be created, reused, or conflicts), then
	 *   applied. Imports never overwrite: an existing slug with other content becomes a new slug
	 *   (`on_conflict=rename`) or the import is refused (`error`).
	 *
	 * Props: `open` (bindable), `exportRefs` (preselected `kind:slug@v` refs; empty hides Export),
	 * `onimported` (called after an apply).
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import Download from '@lucide/svelte/icons/download';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Upload from '@lucide/svelte/icons/upload';
	import { formatApiError } from '$lib/api/core';
	import { exportLibraryPackage, importLibraryPackage } from '$lib/api/v3/library';
	import type { PackageImportOut } from '$lib/api/v3/types';
	import { invalidateLibrary } from './library.svelte';

	interface Props {
		open: boolean;
		exportRefs?: string[];
		onimported?: () => void;
	}

	let { open = $bindable(false), exportRefs = [], onimported }: Props = $props();

	let tab = $state<'export' | 'import'>('import');
	$effect(() => {
		if (open) tab = exportRefs.length ? 'export' : 'import';
	});

	// export
	let withDeps = $state(true);
	let exporting = $state(false);
	let exportError = $state('');

	async function doExport() {
		exporting = true;
		exportError = '';
		try {
			const blob = await exportLibraryPackage(exportRefs, withDeps);
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `${(exportRefs[0] ?? 'library').split(':').pop()?.replace(/[@#]/g, '-') ?? 'library'}.library.yaml`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			exportError = formatApiError(e, 'The export failed.');
		} finally {
			exporting = false;
		}
	}

	// import
	let yamlText = $state('');
	let fileName = $state('');
	let onConflict = $state<'rename' | 'error'>('rename');
	let preview = $state<PackageImportOut | null>(null);
	let applied = $state<PackageImportOut | null>(null);
	let importing = $state(false);
	let importError = $state('');

	async function readFile(e: Event) {
		const file = (e.currentTarget as HTMLInputElement).files?.[0];
		preview = null;
		applied = null;
		importError = '';
		if (!file) return;
		fileName = file.name;
		yamlText = await file.text();
		await run('dry_run');
	}

	async function run(mode: 'dry_run' | 'apply') {
		importing = true;
		importError = '';
		try {
			const out = await importLibraryPackage(yamlText, mode, onConflict);
			if (mode === 'dry_run') preview = out;
			else {
				applied = out;
				invalidateLibrary();
				onimported?.();
			}
		} catch (e) {
			importError = formatApiError(e, 'The import failed.');
			const body = e as Partial<PackageImportOut>;
			if (body.conflicts) preview = { mode, created: body.created ?? [], reused: body.reused ?? [], conflicts: body.conflicts };
		} finally {
			importing = false;
		}
	}

	/** `{kind, ref, as?, resolution?}` rows of the import result. */
	const label = (row: Record<string, unknown>) => {
		const as = row.as && row.as !== row.ref ? ` → ${row.as}` : '';
		const resolution = row.resolution ? ` (${row.resolution})` : '';
		return `${row.kind ?? ''} ${row.ref ?? JSON.stringify(row)}${as}${resolution}`.trim();
	};
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>Library packages</Dialog.Title>
			<Dialog.Description>Move library entities between deployments as YAML (package v4). Versions are never overwritten.</Dialog.Description>
		</Dialog.Header>

		{#if exportRefs.length}
			<Segmented
				aria-label="Export or import"
				value={tab}
				items={[
					{ value: 'export', label: 'Export' },
					{ value: 'import', label: 'Import' },
				]}
				onValueChange={(v) => (tab = v === 'export' ? 'export' : 'import')}
			/>
		{/if}

		{#if tab === 'export' && exportRefs.length}
			<div class="space-y-3">
				<ul class="space-y-0.5 font-mono text-xs">
					{#each exportRefs as ref (ref)}<li>{ref}</li>{/each}
				</ul>
				<label class="flex items-center gap-2 text-sm"><Checkbox bind:checked={withDeps} /> Include dependencies (templates, stack profiles, …)</label>
				{#if exportError}<ErrorAlert message={exportError} />{/if}
				<div class="flex justify-end">
					<Button size="sm" onclick={doExport} disabled={exporting}>
						{#if exporting}<LoaderCircle class="animate-spin" />{:else}<Download />{/if}Download YAML
					</Button>
				</div>
			</div>
		{:else}
			<div class="space-y-3">
				<label class="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-border px-3 py-3 text-sm hover:border-primary/50">
					<Upload class="size-4 text-muted-foreground" aria-hidden="true" />
					<span>{fileName || 'Choose a .yaml package…'}</span>
					<input type="file" accept=".yaml,.yml,application/x-yaml,text/yaml" class="sr-only" onchange={readFile} />
				</label>
				<div class="flex flex-wrap items-center gap-3 text-sm">
					<span class="text-muted-foreground">When a slug exists with other content:</span>
					<Segmented
						aria-label="On conflict"
						size="sm"
						value={onConflict}
						items={[
							{ value: 'rename', label: 'Import under a new slug' },
							{ value: 'error', label: 'Refuse' },
						]}
						onValueChange={(v) => {
							onConflict = v === 'error' ? 'error' : 'rename';
							if (yamlText) void run('dry_run');
						}}
					/>
				</div>
				{#if importError}<ErrorAlert message={importError} />{/if}
				{#if importing}
					<p class="flex items-center gap-2 text-sm text-muted-foreground"><LoaderCircle class="size-4 animate-spin" />Checking the package</p>
				{/if}
				{#each [['Created', (applied ?? preview)?.created], ['Reused (identical content)', (applied ?? preview)?.reused], ['Conflicts', (applied ?? preview)?.conflicts]] as const as [title, rows] (title)}
					{#if rows?.length}
						<div>
							<p class="section-label">{title} <span class="font-mono">{rows.length}</span></p>
							<ul class="max-h-40 space-y-0.5 overflow-y-auto font-mono text-xs">
								{#each rows as row, i (i)}<li>{label(row)}</li>{/each}
							</ul>
						</div>
					{/if}
				{/each}
				{#if applied}
					<p class="text-sm text-success-fg">Imported.</p>
				{/if}
			</div>
			<Dialog.Footer>
				<Button variant="outline" size="sm" onclick={() => (open = false)}>Close</Button>
				<Button size="sm" disabled={!preview || importing || !!applied || (onConflict === 'error' && (preview?.conflicts.length ?? 0) > 0)} onclick={() => run('apply')}>
					Import
				</Button>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
