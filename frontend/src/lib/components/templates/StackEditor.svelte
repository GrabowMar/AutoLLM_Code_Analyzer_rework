<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { untrack } from 'svelte';
	import { slugify } from '$lib/components/templates/slugify';
	import {
		getStackBaseImages,
		getStackDetail,
		previewStackDockerfile,
		type StackDetail,
		type StackWritePayload,
	} from '$lib/api/client';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';
	import Eye from '@lucide/svelte/icons/eye';

	interface Props {
		/** Slug of an existing user stack to edit (creates version+1), or null for new. */
		editSlug: string | null;
		/** Prefill from this stack (duplicate-builtin flow); saved as a new slug. */
		duplicateFromSlug?: string | null;
		saving: boolean;
		error: string;
		onSave: (payload: StackWritePayload, isNewSlug: boolean) => void;
		onCancel: () => void;
	}
	let { editSlug, duplicateFromSlug = null, saving, error, onSave, onCancel }: Props = $props();

	const sourceSlug = untrack(() => editSlug ?? duplicateFromSlug);
	const isNewSlug = untrack(() => editSlug) === null;

	let loading = $state(sourceSlug !== null);
	let name = $state('');
	let slug = $state(untrack(() => editSlug) ?? '');
	let slugTouched = $state(false);
	let description = $state('');
	let hasFrontend = $state(false);
	let defaultPort = $state(8000);
	let patchProfile = $state('none');
	let frontendComponent = $state('');
	let backendFilename = $state('app.py');
	let backendBaseImage = $state('');
	let frontendBaseImage = $state('');
	let serverKind = $state('python');
	let files = $state<Record<string, string>>({ 'requirements.txt': 'flask\n' });
	let selectedPath = $state('requirements.txt');
	let newPath = $state('');
	let currentVersion = $state(1);

	let baseImages = $state<{ python: string[]; node: string[] }>({ python: [], node: [] });

	$effect(() => {
		if (isNewSlug && !slugTouched) slug = slugify(name);
	});

	async function load() {
		try {
			baseImages = await getStackBaseImages();
			if (!backendBaseImage && baseImages.python.length) backendBaseImage = baseImages.python[0];
			if (!frontendBaseImage && baseImages.node.length) frontendBaseImage = baseImages.node[0];
			if (sourceSlug) {
				const detail: StackDetail = await getStackDetail(sourceSlug);
				name = editSlug ? detail.name : `${detail.name || detail.slug} (copy)`;
				description = detail.description;
				hasFrontend = detail.has_frontend;
				defaultPort = detail.default_port;
				patchProfile = detail.patch_profile;
				frontendComponent = detail.frontend_component;
				backendFilename = detail.backend_filename || 'app.py';
				backendBaseImage = detail.backend_base_image || baseImages.python[0] || '';
				frontendBaseImage = detail.frontend_base_image || baseImages.node[0] || '';
				serverKind = detail.server_kind || 'python';
				currentVersion = detail.version;
				// Builtins bundle their Dockerfile; user copies get a generated one.
				files = Object.fromEntries(
					Object.entries(detail.files).filter(([path]) => {
						const base = path.split('/').pop()?.toLowerCase() ?? '';
						return base !== 'dockerfile' && base !== 'docker-compose.yml' && base !== '.dockerignore';
					}),
				);
				selectedPath = Object.keys(files)[0] ?? '';
			}
		} finally {
			loading = false;
		}
	}
	load();

	function addFile() {
		const path = newPath.trim();
		if (!path || files[path] !== undefined) return;
		files = { ...files, [path]: '' };
		selectedPath = path;
		newPath = '';
	}

	function removeFile(path: string) {
		const { [path]: _removed, ...rest } = files;
		files = rest;
		if (selectedPath === path) selectedPath = Object.keys(files)[0] ?? '';
	}

	function payload(): StackWritePayload {
		return {
			slug,
			name,
			description,
			has_frontend: hasFrontend,
			default_port: defaultPort,
			patch_profile: patchProfile,
			frontend_component: frontendComponent,
			backend_filename: backendFilename,
			backend_base_image: backendBaseImage,
			frontend_base_image: hasFrontend ? frontendBaseImage : '',
			server_kind: serverKind,
			files: $state.snapshot(files) as Record<string, string>,
		};
	}

	// Dockerfile preview
	let dockerfileOpen = $state(false);
	let dockerfileText = $state('');
	let dockerfileError = $state('');
	let dockerfileLoading = $state(false);

	async function loadDockerfile() {
		dockerfileLoading = true;
		dockerfileError = '';
		try {
			const res = await previewStackDockerfile(payload());
			dockerfileText = res.dockerfile;
		} catch (e: any) {
			dockerfileError = e?.detail ?? e?.message ?? 'Preview failed';
		}
		dockerfileLoading = false;
	}
</script>

<Card.Root>
	<Card.Header class="pb-3">
		<Card.Title class="text-base">
			{#if editSlug}
				Edit {editSlug}
				<span class="text-xs font-normal text-muted-foreground">creates v{currentVersion + 1} — versions are immutable</span>
			{:else if duplicateFromSlug}
				Duplicate {duplicateFromSlug} as a new stack
			{:else}
				New Stack
			{/if}
		</Card.Title>
		<Card.Description class="text-xs">
			User stacks cannot ship a Dockerfile — it is generated from a pinned template with allowlisted base images.
		</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if loading}
			<div class="flex items-center gap-2 py-8 text-sm text-muted-foreground">
				<LoaderCircle class="h-4 w-4 animate-spin" /> Loading…
			</div>
		{:else}
			<div class="grid gap-3 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-name">Name</Label>
					<Input id="stack-name" bind:value={name} class="h-9 text-sm" />
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-slug">Slug</Label>
					<Input id="stack-slug" bind:value={slug} disabled={!isNewSlug} oninput={() => (slugTouched = true)} class="h-9 text-sm font-mono" />
				</div>
			</div>
			<div class="space-y-1.5">
				<Label class="text-xs" for="stack-description">Description</Label>
				<Input id="stack-description" bind:value={description} class="h-9 text-sm" />
			</div>

			<div class="grid gap-3 sm:grid-cols-3">
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-backend-image">Backend base image</Label>
					<select id="stack-backend-image" bind:value={backendBaseImage} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs">
						{#each baseImages.python as img}<option value={img}>{img}</option>{/each}
					</select>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-server-kind">Server</Label>
					<select id="stack-server-kind" bind:value={serverKind} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs">
						<option value="python">python {backendFilename}</option>
						<option value="uvicorn">uvicorn {backendFilename.replace('.py', '')}:app</option>
					</select>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-port">Port</Label>
					<input id="stack-port" type="number" bind:value={defaultPort} min={1024} max={65535} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs" />
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-backend-filename">Backend file</Label>
					<Input id="stack-backend-filename" bind:value={backendFilename} class="h-9 text-sm font-mono" />
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="stack-patch">Patch profile</Label>
					<select id="stack-patch" bind:value={patchProfile} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs">
						<option value="none">none</option>
						<option value="flask">flask (sqlite/port/create_all fixes)</option>
					</select>
				</div>
				<label class="flex items-center gap-2 self-end pb-2 text-xs cursor-pointer">
					<input type="checkbox" bind:checked={hasFrontend} class="accent-primary" />
					Has frontend (built with npm)
				</label>
			</div>

			{#if hasFrontend}
				<div class="grid gap-3 sm:grid-cols-2">
					<div class="space-y-1.5">
						<Label class="text-xs" for="stack-frontend-image">Frontend base image</Label>
						<select id="stack-frontend-image" bind:value={frontendBaseImage} class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs">
							{#each baseImages.node as img}<option value={img}>{img}</option>{/each}
						</select>
					</div>
					<div class="space-y-1.5">
						<Label class="text-xs" for="stack-frontend-component">Main component</Label>
						<Input id="stack-frontend-component" bind:value={frontendComponent} placeholder="App.jsx" class="h-9 text-sm font-mono" />
					</div>
				</div>
			{/if}

			<!-- File tree editor -->
			<div class="space-y-2">
				<Label class="text-xs">Skeleton files <span class="font-normal text-muted-foreground">(≤64 files, text only; Dockerfile is generated)</span></Label>
				<div class="grid gap-2 sm:grid-cols-[220px_1fr]">
					<div class="space-y-1">
						<div class="max-h-56 space-y-0.5 overflow-y-auto rounded border bg-muted/20 p-1">
							{#each Object.keys(files).sort() as path (path)}
								<div class="flex items-center gap-1">
									<button
										type="button"
										class="flex-1 truncate rounded px-2 py-1 text-left text-[11px] font-mono transition-colors cursor-pointer {selectedPath === path ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:text-foreground'}"
										onclick={() => (selectedPath = path)}
									>{path}</button>
									<button type="button" class="p-1 text-muted-foreground hover:text-destructive cursor-pointer" onclick={() => removeFile(path)} aria-label="Remove {path}">
										<X class="h-3 w-3" />
									</button>
								</div>
							{/each}
						</div>
						<div class="flex gap-1">
							<input
								type="text"
								bind:value={newPath}
								placeholder="frontend/src/App.jsx"
								class="h-7 flex-1 rounded border border-input bg-surface-1 px-2 text-[11px] font-mono"
								onkeydown={(e) => e.key === 'Enter' && addFile()}
							/>
							<Button variant="outline" size="sm" class="h-7 px-2 text-xs cursor-pointer" onclick={addFile}>
								<Plus class="h-3 w-3" />
							</Button>
						</div>
					</div>
					{#if selectedPath && files[selectedPath] !== undefined}
						<Textarea
							bind:value={files[selectedPath]}
							rows={12}
							class="text-xs font-mono leading-relaxed"
							placeholder="file content"
						/>
					{:else}
						<div class="flex items-center justify-center rounded border border-dashed text-xs text-muted-foreground">Select or add a file</div>
					{/if}
				</div>
			</div>

			<!-- Dockerfile preview -->
			<div class="space-y-2">
				<button
					type="button"
					class="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
					onclick={() => { dockerfileOpen = !dockerfileOpen; if (dockerfileOpen) loadDockerfile(); }}
				>
					<Eye class="h-3.5 w-3.5" />
					{dockerfileOpen ? 'Hide generated Dockerfile' : 'Preview generated Dockerfile'}
				</button>
				{#if dockerfileOpen}
					{#if dockerfileLoading}
						<div class="flex items-center gap-2 text-xs text-muted-foreground"><LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Rendering…</div>
					{:else if dockerfileError}
						<p class="text-[10px] text-destructive">{dockerfileError}</p>
					{:else}
						<pre class="max-h-56 overflow-auto rounded border bg-muted/20 p-2 text-[10px] leading-relaxed whitespace-pre-wrap">{dockerfileText}</pre>
					{/if}
				{/if}
			</div>

			{#if error}
				<div class="rounded-md bg-destructive/10 border border-destructive/30 px-4 py-3 text-sm text-destructive">{error}</div>
			{/if}

			<div class="flex items-center justify-end gap-2 border-t pt-4">
				<Button variant="outline" size="sm" onclick={onCancel} disabled={saving}>Cancel</Button>
				<Button size="sm" onclick={() => onSave(payload(), isNewSlug)} disabled={saving || !name.trim() || !slug.trim()}>
					{#if saving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />{/if}
					{isNewSlug ? 'Create stack' : `Save as v${currentVersion + 1}`}
				</Button>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
