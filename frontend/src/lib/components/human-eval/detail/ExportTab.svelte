<script lang="ts">
	/**
	 * ExportTab — download links for the task's data: the ZIP bundle (every
	 * CSV + design.json, settings.json, results.json and a codebook), the
	 * results as JSON, and each long-format CSV. CSVs always carry every row
	 * (preview data excluded) with exclusion and quality columns, so analysis
	 * scripts decide; `include_excluded` only changes the computed results.
	 * Links are same-origin downloads authenticated by the session cookie.
	 *
	 * Props: `task`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { exportBundleUrl, exportCsvUrl, exportResultsJsonUrl, type BlockType, type TaskDetail } from '$lib/api/human-eval';
	import { EXPORT_KINDS, EXPORT_KIND_BLOCK, shortHash, slugify } from '../format';
	import Braces from '@lucide/svelte/icons/braces';
	import Download from '@lucide/svelte/icons/download';
	import FileArchive from '@lucide/svelte/icons/file-archive';
	import FileSpreadsheet from '@lucide/svelte/icons/file-spreadsheet';

	let { task }: { task: TaskDetail } = $props();

	const slug = $derived(slugify(task.title));
	const blockTypes = $derived(new Set<BlockType>(task.design.blocks.map((b) => b.type)));
	const hasConsole = $derived(task.design.blocks.some((b) => b.type === 'security' && b.http_console));
	const kinds = $derived(
		EXPORT_KINDS.map((k) => {
			const needs = EXPORT_KIND_BLOCK[k.kind];
			const applies = k.kind === 'http_log' ? hasConsole : !needs || blockTypes.has(needs);
			return { ...k, applies };
		})
	);
</script>

<div class="space-y-4">
	<div class="grid gap-4 md:grid-cols-2">
		<Card.Root class="gap-3">
			<Card.Header>
				<div class="flex items-center gap-2">
					<FileArchive class="size-4 text-muted-foreground" aria-hidden="true" />
					<Card.Title>Full bundle (ZIP)</Card.Title>
				</div>
				<Card.Description>
					Every CSV below plus design.json, settings.json, results.json and codebook.md (column dictionary, design hash
					{#if task.design_hash}<span class="font-mono">{shortHash(task.design_hash)}</span>{/if}, seed {task.seed}, exclusion rules).
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<Button size="sm" href={exportBundleUrl(task.id)} download="human-eval-{slug}-bundle.zip"><Download />Download bundle</Button>
			</Card.Content>
		</Card.Root>
		<Card.Root class="gap-3">
			<Card.Header>
				<div class="flex items-center gap-2">
					<Braces class="size-4 text-muted-foreground" aria-hidden="true" />
					<Card.Title>Results (JSON)</Card.Title>
				</div>
				<Card.Description>
					The computed results as shown on the Results tab (grouped by condition, excluded data left out), for scripts and archiving.
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<Button variant="outline" size="sm" href={exportResultsJsonUrl(task.id)} download="human-eval-{slug}-results.json"><Download />Download results.json</Button>
			</Card.Content>
		</Card.Root>
	</div>

	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="border-b border-border py-3">
			<Card.Title>Tables (CSV)</Card.Title>
			<Card.Description>
				Long format, one file per kind. Every row is included (your preview excluded) with assignment status, exclusion, quality-flag
				and rule-exclusion columns; participants are pseudonymous (no emails, codes or IP addresses). Model names appear only in the
				apps table — the de-blinding key.
			</Card.Description>
		</Card.Header>
		<ul class="divide-y divide-border/60">
			{#each kinds as k (k.kind)}
				<li class="flex flex-wrap items-center justify-between gap-3 px-4 py-2.5">
					<div class="flex min-w-0 items-start gap-2.5">
						<FileSpreadsheet class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
						<div class="min-w-0">
							<p class="text-sm font-medium">
								{k.label}
								<span class="ml-1 font-mono text-[11px] font-normal text-muted-foreground">{k.kind}.csv</span>
							</p>
							<p class="text-xs text-muted-foreground">
								{k.description}
								{#if !k.applies}<span class="italic"> No matching block in this design: header only.</span>{/if}
							</p>
						</div>
					</div>
					<Button
						variant="ghost"
						size="sm"
						href={exportCsvUrl(task.id, k.kind)}
						download="human-eval-{slug}-{k.kind}.csv"
						aria-label="Download {k.label} CSV"
					>
						<Download />CSV
					</Button>
				</li>
			{/each}
		</ul>
	</Card.Root>
</div>
