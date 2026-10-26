<script lang="ts" module>
	export interface CodeFile {
		key: number;
		path: string;
		content: string;
	}

	let nextKey = 1;
	export function newFile(path = '', content = ''): CodeFile {
		return { key: nextKey++, path, content };
	}

	/** `{path: content}` of the non-empty files (what the API takes). */
	export function toSourceCode(files: CodeFile[]): Record<string, string> {
		const out: Record<string, string> = {};
		for (const f of files) {
			const path = f.path.trim().replace(/^\/+/, '');
			if (path && f.content.trim()) out[path] = f.content;
		}
		return out;
	}
</script>

<script lang="ts">
	/**
	 * CodeFilesEditor — pasted code as named files. File names matter: they
	 * decide which tools apply (pip-audit reads requirements.txt, hadolint a
	 * Dockerfile, ESLint *.js / *.jsx …).
	 *
	 * Props: `files` (bindable, replaced on change).
	 */
	import { Input } from '$lib/components/ui/input';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	let { files = $bindable() }: { files: CodeFile[] } = $props();

	const duplicates = $derived.by(() => {
		const seen = new Map<string, number>();
		for (const f of files) {
			const p = f.path.trim();
			if (p) seen.set(p, (seen.get(p) ?? 0) + 1);
		}
		return new Set([...seen].filter(([, n]) => n > 1).map(([p]) => p));
	});

	function update(key: number, patch: Partial<CodeFile>) {
		files = files.map((f) => (f.key === key ? { ...f, ...patch } : f));
	}
</script>

<div class="space-y-3">
	{#each files as file, i (file.key)}
		{@const dup = duplicates.has(file.path.trim())}
		<div class="overflow-hidden rounded-md border border-border">
			<div class="flex items-center gap-2 border-b border-border bg-muted/40 px-2 py-1.5">
				<Input
					value={file.path}
					oninput={(e) => update(file.key, { path: (e.currentTarget as HTMLInputElement).value })}
					placeholder="path/to/file.py"
					aria-label="File {i + 1} path"
					aria-invalid={dup || (!file.path.trim() && !!file.content.trim())}
					class="h-8 flex-1 font-mono text-xs"
				/>
				<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{file.content ? file.content.split('\n').length : 0} lines</span>
				<Button variant="ghost" size="sm" class="size-8 p-0" disabled={files.length === 1} onclick={() => (files = files.filter((f) => f.key !== file.key))} aria-label="Remove file {file.path || i + 1}">
					<Trash2 class="size-4" />
				</Button>
			</div>
			<textarea
				value={file.content}
				oninput={(e) => update(file.key, { content: (e.currentTarget as HTMLTextAreaElement).value })}
				class="block h-48 w-full resize-y bg-background p-3 font-mono text-xs leading-5 focus-visible:outline-none"
				placeholder="Paste the file's content…"
				spellcheck="false"
				aria-label="File {i + 1} content"
			></textarea>
			{#if dup}<p class="border-t border-border px-3 py-1 text-xs text-danger-fg">Two files share this path.</p>{/if}
		</div>
	{/each}
	<div class="flex flex-wrap items-center gap-3">
		<Button variant="outline" size="sm" onclick={() => (files = [...files, newFile()])}><Plus />Add file</Button>
		<p class="text-xs text-muted-foreground">
			File names decide which tools apply — e.g. <span class="font-mono">requirements.txt</span> for pip-audit,
			<span class="font-mono">Dockerfile</span> for hadolint, <span class="font-mono">*.jsx</span> for ESLint.
		</p>
	</div>
</div>
