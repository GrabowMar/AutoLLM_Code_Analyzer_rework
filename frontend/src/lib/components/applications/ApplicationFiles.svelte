<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import FolderTree from '@lucide/svelte/icons/folder-tree';
import EmptyState from '$lib/components/EmptyState.svelte';
import FileText from '@lucide/svelte/icons/file-text';
import FileCode from '@lucide/svelte/icons/file-code';
import Eye from '@lucide/svelte/icons/eye';
import Copy from '@lucide/svelte/icons/copy';
import { copyText, type VirtualFile } from './utils';

interface Props {
	files: VirtualFile[];
}

let { files }: Props = $props();

let selectedFileIdx = $state(0);

const keyArtifactFiles = $derived.by(() => {
	const notable = ['docker-compose.yml', 'docker-compose.yaml', 'readme.md', 'requirements.txt', 'package.json', 'dockerfile', '.env', 'manage.py', 'app.py', 'index.html'];
	const found: { name: string; idx: number; subtitle: string }[] = [];
	for (let i = 0; i < files.length; i++) {
		const f = files[i];
		const basename = (f.name.split('/').pop() ?? f.name).toLowerCase();
		if (notable.includes(basename)) {
			let subtitle = 'File';
			if (basename.includes('docker-compose')) subtitle = 'Docker Compose';
			else if (basename === 'dockerfile') subtitle = 'Docker';
			else if (basename === 'readme.md') subtitle = 'Documentation';
			else if (basename === 'requirements.txt') subtitle = 'Python deps';
			else if (basename === 'package.json') subtitle = 'Node.js deps';
			else if (basename === '.env') subtitle = 'Environment';
			else if (basename === 'manage.py') subtitle = 'Django management';
			else if (basename === 'app.py') subtitle = 'Application entry';
			else if (basename === 'index.html') subtitle = 'HTML entry';
			found.push({ name: f.name.split('/').pop() ?? f.name, idx: i, subtitle });
		}
	}
	return found;
});

const fileStats = $derived.by(() => {
	const codeExts = new Set(['py', 'js', 'jsx', 'ts', 'tsx', 'html', 'css', 'svelte', 'vue']);
	const configExts = new Set(['json', 'yml', 'yaml', 'toml', 'env', 'txt', 'cfg', 'ini']);
	let totalSize = 0, codeCount = 0, configCount = 0;
	for (const f of files) {
		totalSize += f.code.length;
		const ext = (f.name.split('.').pop() ?? '').toLowerCase();
		if (codeExts.has(ext)) codeCount++;
		else if (configExts.has(ext)) configCount++;
	}
	return { totalSize, codeCount, configCount, totalFiles: files.length };
});

const extensionBreakdown = $derived.by(() => {
	const exts: Record<string, { count: number; size: number }> = {};
	for (const f of files) {
		const ext = '.' + ((f.name.split('.').pop() ?? 'other').toLowerCase());
		if (!exts[ext]) exts[ext] = { count: 0, size: 0 };
		exts[ext].count++;
		exts[ext].size += f.code.length;
	}
	const total = files.length || 1;
	return Object.entries(exts)
		.sort((a, b) => b[1].count - a[1].count)
		.map(([ext, data]) => ({ ext, ...data, pct: (data.count / total) * 100 }));
});
</script>

<section id="files" class="space-y-4">
	{#if files.length === 0}
		<Card.Root>
			<Card.Content>
				<EmptyState compact icon={FolderTree} title="No files generated" description="This job produced no code output." />
			</Card.Content>
		</Card.Root>
	{:else}
		<!-- Key Artifacts Card -->
		{#if keyArtifactFiles.length > 0}
			<Card.Root>
				<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">Key Artifacts</Card.Title><Card.Description>Entry points, manifests and config detected in the output.</Card.Description></Card.Header>
				<Card.Content class="p-0">
					<div class="divide-y">
						{#each keyArtifactFiles as artifact}
							<div class="flex items-center gap-3 px-4 py-2.5">
								<FileText class="h-4 w-4 shrink-0 text-muted-foreground" />
								<div class="flex-1 min-w-0">
									<div class="text-sm font-medium font-mono">{artifact.name}</div>
									<div class="text-xs text-muted-foreground">{artifact.subtitle}</div>
								</div>
								<Button variant="ghost" size="sm" class="h-7 w-7 p-0" onclick={() => (selectedFileIdx = artifact.idx)} title="View file" aria-label="View {artifact.name}">
									<Eye class="h-3.5 w-3.5" />
								</Button>
							</div>
						{/each}
					</div>
				</Card.Content>
			</Card.Root>
		{/if}

		<!-- File Explorer -->
		<Card.Root>
			<Card.Header>
				<Card.Title>File explorer</Card.Title>
				<Card.Description>
					{fileStats.totalFiles} files · {(fileStats.totalSize / 1024).toFixed(1)} KB · {fileStats.codeCount} code · {fileStats.configCount} config
				</Card.Description>
			</Card.Header>
			<Card.Content class="border-t p-0">
				<div class="flex max-h-[650px] min-h-[300px] flex-col sm:flex-row">
					<!-- Left: File Tree -->
					<div class="w-full sm:w-1/3 border-b sm:border-b-0 sm:border-r overflow-y-auto bg-muted/20 max-h-48 sm:max-h-none">
						<div class="section-label border-b px-3 py-2">
							Files ({files.length})
						</div>
						{#each files as f, i}
							<button
								class="w-full text-left px-3 py-2.5 text-sm border-b border-border/50 transition-colors flex items-center gap-2 {selectedFileIdx === i ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'}"
								onclick={() => (selectedFileIdx = i)}
							>
								<FileCode class="h-4 w-4 text-muted-foreground shrink-0" />
								<div class="min-w-0 flex-1">
									<div class="font-medium truncate font-mono text-xs">{f.name}</div>
									<div class="text-xs text-muted-foreground">{f.code.split('\n').length} lines · {(f.code.length / 1024).toFixed(1)} KB</div>
								</div>
							</button>
						{/each}
					</div>
					<!-- Right: File Preview -->
					<div class="w-full sm:w-2/3 flex flex-col min-h-[200px]">
						<div class="flex items-center justify-between px-4 py-2 border-b bg-muted/20">
							<span class="text-sm font-medium font-mono">{files[selectedFileIdx]?.name ?? ''}</span>
							<div class="flex items-center gap-1">
								<Badge variant="outline" class="text-xs">{files[selectedFileIdx]?.lang}</Badge>
								<Button variant="ghost" size="sm" class="h-7" title="Copy file" aria-label="Copy file" onclick={() => copyText(files[selectedFileIdx]?.code ?? '', 'Copied file')}>
									<Copy class="h-3.5 w-3.5" />
								</Button>
							</div>
						</div>
						<div class="flex-1 overflow-auto bg-muted/40 max-w-full">
							<pre class="p-4 text-xs font-mono text-foreground/90 leading-relaxed overflow-x-auto">{#each (files[selectedFileIdx]?.code ?? '').split('\n') as line, ln}<span class="inline-block w-10 text-right mr-4 text-muted-foreground/60 select-none">{ln + 1}</span>{line}
{/each}</pre>
						</div>
					</div>
				</div>
			</Card.Content>
		</Card.Root>

		<!-- By Extension Breakdown -->
		{#if extensionBreakdown.length > 0}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3"><Card.Title class="text-sm font-medium">By Extension</Card.Title><Card.Description>Share of generated files per extension.</Card.Description></Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
					<table>
						<thead>
							<tr>
								<th>Extension</th>
								<th class="num">Files</th>
								<th class="num">Size</th>
								<th class="w-1/3">%</th>
							</tr>
						</thead>
						<tbody>
							{#each extensionBreakdown as ext}
								<tr>
									<td class="font-mono text-xs font-medium">{ext.ext}</td>
									<td class="num">{ext.count}</td>
									<td class="num text-muted-foreground">{(ext.size / 1024).toFixed(1)} KB</td>
									<td>
										<div class="flex items-center gap-2">
											<div class="flex-1 h-2 rounded-full bg-muted overflow-hidden">
												<div class="h-full rounded-full {ext.pct > 30 ? 'bg-primary' : ext.pct > 10 ? 'bg-primary/60' : 'bg-muted-foreground/50'}" style="width: {ext.pct}%"></div>
											</div>
											<span class="w-10 text-right font-mono text-xs tabular-nums text-muted-foreground">{ext.pct.toFixed(0)}%</span>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}
	{/if}
</section>
