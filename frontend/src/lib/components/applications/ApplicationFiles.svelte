<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import FolderTree from '@lucide/svelte/icons/folder-tree';
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
	const found: { name: string; idx: number; icon: string; subtitle: string }[] = [];
	for (let i = 0; i < files.length; i++) {
		const f = files[i];
		const basename = (f.name.split('/').pop() ?? f.name).toLowerCase();
		if (notable.includes(basename)) {
			let icon = '📄', subtitle = 'File';
			if (basename.includes('docker-compose')) { icon = '🐳'; subtitle = 'Docker Compose'; }
			else if (basename === 'dockerfile') { icon = '🐳'; subtitle = 'Docker'; }
			else if (basename === 'readme.md') { icon = '📖'; subtitle = 'Documentation'; }
			else if (basename === 'requirements.txt') { icon = '📦'; subtitle = 'Python deps'; }
			else if (basename === 'package.json') { icon = '📦'; subtitle = 'Node.js deps'; }
			else if (basename === '.env') { icon = '🔧'; subtitle = 'Environment'; }
			else if (basename === 'manage.py') { icon = '🐍'; subtitle = 'Django management'; }
			else if (basename === 'app.py') { icon = '🐍'; subtitle = 'Application entry'; }
			else if (basename === 'index.html') { icon = '🌐'; subtitle = 'HTML entry'; }
			found.push({ name: f.name.split('/').pop() ?? f.name, idx: i, icon, subtitle });
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
	<h2 class="text-lg font-semibold flex items-center gap-2"><FolderTree class="h-5 w-5" /> Files & Code</h2>
	{#if files.length === 0}
		<Card.Root>
			<Card.Content class="py-12 text-center">
				<FolderTree class="mx-auto h-10 w-10 text-muted-foreground/30 mb-3" />
				<p class="text-sm text-muted-foreground">No files generated</p>
			</Card.Content>
		</Card.Root>
	{:else}
		<!-- Key Artifacts Card -->
		{#if keyArtifactFiles.length > 0}
			<Card.Root>
				<Card.Header class="pb-2"><Card.Title class="text-sm font-medium flex items-center gap-1.5"><FileText class="h-3.5 w-3.5" /> Key Artifacts</Card.Title></Card.Header>
				<Card.Content class="p-0">
					<div class="divide-y">
						{#each keyArtifactFiles as artifact}
							<div class="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/30 transition-colors">
								<span class="text-lg">{artifact.icon}</span>
								<div class="flex-1 min-w-0">
									<div class="text-sm font-medium font-mono">{artifact.name}</div>
									<div class="text-xs text-muted-foreground">{artifact.subtitle}</div>
								</div>
								<Button variant="ghost" size="sm" class="h-7 w-7 p-0" onclick={() => (selectedFileIdx = artifact.idx)} title="View file">
									<Eye class="h-3.5 w-3.5" />
								</Button>
							</div>
						{/each}
					</div>
				</Card.Content>
			</Card.Root>
		{/if}

		<!-- File Stats Badges -->
		<div class="flex items-center gap-2 flex-wrap">
			<Badge variant="outline" class="text-xs bg-primary/10 text-primary border-primary/30">📁 {fileStats.totalFiles} Files</Badge>
			<Badge variant="outline" class="text-xs bg-emerald-500/10 text-emerald-500 border-emerald-500/30">📊 {(fileStats.totalSize / 1024).toFixed(1)} KB</Badge>
			<Badge variant="outline" class="text-xs bg-sky-500/10 text-sky-400 border-sky-500/30">&lt;/&gt; {fileStats.codeCount} Code</Badge>
			<Badge variant="outline" class="text-xs bg-amber-500/10 text-amber-400 border-amber-500/30">⚙️ {fileStats.configCount} Config</Badge>
		</div>

		<!-- File Explorer -->
		<Card.Root>
			<Card.Content class="p-0">
				<div class="flex flex-col sm:flex-row" style="min-height: 300px; height: auto; max-height: 650px;">
					<!-- Left: File Tree -->
					<div class="w-full sm:w-1/3 border-b sm:border-b-0 sm:border-r overflow-y-auto bg-muted/20 max-h-48 sm:max-h-none">
						<div class="p-2 text-xs font-medium text-muted-foreground uppercase tracking-wider border-b px-3 py-2">
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
								<Button variant="ghost" size="sm" class="h-7" onclick={() => copyText(files[selectedFileIdx]?.code ?? '', 'Copied file')}>
									<Copy class="h-3.5 w-3.5" />
								</Button>
							</div>
						</div>
						<div class="flex-1 overflow-auto bg-zinc-950 max-w-full">
							<pre class="p-4 text-xs font-mono text-zinc-300 leading-relaxed overflow-x-auto">{#each (files[selectedFileIdx]?.code ?? '').split('\n') as line, ln}<span class="inline-block w-10 text-right mr-4 text-zinc-600 select-none">{ln + 1}</span>{line}
{/each}</pre>
						</div>
					</div>
				</div>
			</Card.Content>
		</Card.Root>

		<!-- By Extension Breakdown -->
		{#if extensionBreakdown.length > 0}
			<Card.Root>
				<Card.Header class="pb-2"><Card.Title class="text-sm font-medium">By Extension</Card.Title></Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b bg-muted/30">
								<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Extension</th>
								<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Files</th>
								<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Size</th>
								<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground w-1/3">%</th>
							</tr>
						</thead>
						<tbody class="divide-y">
							{#each extensionBreakdown as ext}
								<tr class="hover:bg-muted/30">
									<td class="px-4 py-2 font-mono text-xs font-medium">{ext.ext}</td>
									<td class="px-4 py-2">{ext.count}</td>
									<td class="px-4 py-2 text-muted-foreground">{(ext.size / 1024).toFixed(1)} KB</td>
									<td class="px-4 py-2">
										<div class="flex items-center gap-2">
											<div class="flex-1 h-2 rounded-full bg-zinc-800 overflow-hidden">
												<div class="h-full rounded-full {ext.pct > 30 ? 'bg-primary' : ext.pct > 10 ? 'bg-sky-500' : 'bg-zinc-600'}" style="width: {ext.pct}%"></div>
											</div>
											<span class="text-xs text-muted-foreground w-10 text-right">{ext.pct.toFixed(0)}%</span>
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
