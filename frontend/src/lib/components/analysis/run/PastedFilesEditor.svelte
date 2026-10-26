<script lang="ts" module>
	export interface PastedFile {
		id: number;
		path: string;
		content: string;
	}

	/** Same limits and path rules as the API (`POST /runs/` `source_code`). */
	export const MAX_PASTED_FILES = 500;
	export const MAX_PASTED_BYTES = 5 * 1024 * 1024;

	/** Per-file path problems ("" when the path is fine). */
	export function pathProblem(path: string, all: PastedFile[]): string {
		const p = path.trim();
		if (!p) return 'Enter a relative path, e.g. backend/app.py';
		if (p.startsWith('/')) return 'Use a relative path (no leading /)';
		if (p.includes('\\')) return 'Use forward slashes';
		if (p.split('/').some((part) => part === '' || part === '.' || part === '..')) {
			return "No empty, '.' or '..' path segments";
		}
		if (all.filter((f) => f.path.trim() === p).length > 1) return 'Duplicate path';
		return '';
	}

	/** The `{path: text}` payload, or null while any file is invalid. */
	export function toSourceCode(files: PastedFile[]): Record<string, string> | null {
		if (files.length === 0 || files.length > MAX_PASTED_FILES) return null;
		if (files.some((f) => pathProblem(f.path, files) || !f.content.trim())) return null;
		const out: Record<string, string> = {};
		for (const f of files) out[f.path.trim()] = f.content;
		if (new TextEncoder().encode(Object.values(out).join('')).length > MAX_PASTED_BYTES)
			return null;
		return out;
	}
</script>

<script lang="ts">
	/**
	 * PastedFilesEditor — files pasted by hand for an ad-hoc run: one path +
	 * content pair per file, validated like the API does (relative POSIX
	 * paths, no duplicates, ≤ 500 files, ≤ 5 MB).
	 *
	 * Props: `files` (bindable, replaced on every edit), `error` (the API's
	 * `source_code` message), `onchange()`.
	 */
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { fmtBytes } from '../run-utils';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	interface Props {
		files?: PastedFile[];
		error?: string;
		onchange?: () => void;
	}

	let { files = $bindable([]), error = '', onchange }: Props = $props();

	let nextId = 1;

	function add() {
		nextId = Math.max(nextId, ...files.map((f) => f.id + 1));
		files = [...files, { id: nextId++, path: '', content: '' }];
		onchange?.();
	}

	function remove(id: number) {
		files = files.filter((f) => f.id !== id);
		onchange?.();
	}

	function edit(id: number, patch: Partial<PastedFile>) {
		files = files.map((f) => (f.id === id ? { ...f, ...patch } : f));
		onchange?.();
	}

	const totalBytes = $derived(
		new TextEncoder().encode(files.map((f) => f.content).join('')).length
	);
</script>

<div class="space-y-3">
	{#each files as file, i (file.id)}
		{@const problem = pathProblem(file.path, files)}
		<div class="space-y-2 rounded-md border border-border p-3">
			<div class="flex items-start gap-2">
				<div class="min-w-0 flex-1">
					<label class="sr-only" for="pasted-path-{file.id}">Path of file {i + 1}</label>
					<Input
						id="pasted-path-{file.id}"
						value={file.path}
						oninput={(e) => edit(file.id, { path: e.currentTarget.value })}
						placeholder="backend/app.py"
						class="h-8 font-mono text-xs"
						aria-invalid={!!problem && file.path !== ''}
						aria-describedby={problem && file.path !== ''
							? `pasted-path-err-${file.id}`
							: undefined}
						autocomplete="off"
						spellcheck="false"
					/>
					{#if problem && file.path !== ''}
						<p id="pasted-path-err-{file.id}" class="mt-1 text-[11px] text-danger-fg">
							{problem}
						</p>
					{/if}
				</div>
				<Button
					variant="ghost"
					size="icon"
					class="size-8 shrink-0"
					onclick={() => remove(file.id)}
					disabled={files.length === 1}
					aria-label="Remove file {i + 1}"
				>
					<Trash2 class="size-3.5" />
				</Button>
			</div>
			<label class="sr-only" for="pasted-content-{file.id}">Content of file {i + 1}</label>
			<Textarea
				id="pasted-content-{file.id}"
				value={file.content}
				oninput={(e: Event & { currentTarget: HTMLTextAreaElement }) =>
					edit(file.id, { content: e.currentTarget.value })}
				rows={8}
				placeholder="Paste the file's source code"
				class="font-mono text-xs"
				spellcheck="false"
			/>
		</div>
	{/each}

	<div class="flex flex-wrap items-center justify-between gap-2">
		<Button variant="outline" size="sm" onclick={add} disabled={files.length >= MAX_PASTED_FILES}>
			<Plus class="size-3.5" />Add file
		</Button>
		<span class="text-[11px] text-muted-foreground tabular-nums">
			{files.length} file{files.length === 1 ? '' : 's'} · {fmtBytes(totalBytes)} of 5 MB
		</span>
	</div>
	{#if error}
		<p class="text-xs text-danger-fg" role="alert">{error}</p>
	{/if}
</div>
