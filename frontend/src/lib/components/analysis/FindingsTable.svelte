<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import LoaderCircle from '@lucide/svelte/icons/loader-circle';
import ChevronDown from '@lucide/svelte/icons/chevron-down';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import FileText from '@lucide/svelte/icons/file-text';
import { severityColors } from '$lib/constants/analysis';
import type { AnalysisFinding, AnalysisResult } from '$lib/api/client';

interface Pagination {
	page: number;
	pages: number;
	total: number;
}

interface Props {
	result: AnalysisResult;
	findings: AnalysisFinding[] | undefined;
	pagination: Pagination | undefined;
	loading: boolean;
	expandedFindings: Record<number, boolean>;
	onLoadFindings: (result: AnalysisResult, page?: number) => void;
	onToggleExpand: (id: number) => void;
}

let {
	result,
	findings,
	pagination,
	loading,
	expandedFindings,
	onLoadFindings,
	onToggleExpand,
}: Props = $props();
</script>

{#if !findings}
	<Button variant="outline" size="sm" onclick={() => onLoadFindings(result)} disabled={loading}>
		{#if loading}
			<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" />
			Loading…
		{:else}
			<FileText class="mr-1.5 h-3.5 w-3.5" />
			Load {result.findings_count} findings
		{/if}
	</Button>
{:else}
	<Card.Root>
		<Card.Content class="p-0">
			<div class="overflow-x-auto">
				<table class="w-full text-sm">
					<thead>
						<tr class="border-b bg-muted/30">
							<th class="w-8 px-4 py-2"></th>
							<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Severity</th>
							<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Title</th>
							<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">File</th>
							<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Line</th>
							<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Rule</th>
							<th class="px-4 py-2 text-left text-xs font-medium text-muted-foreground">Confidence</th>
						</tr>
					</thead>
					<tbody class="divide-y">
						{#each findings as finding (finding.id)}
							<tr
								class="hover:bg-muted/30 cursor-pointer"
								onclick={() => onToggleExpand(finding.id)}
								onkeydown={(e) => { if (e.key === 'Enter') onToggleExpand(finding.id); }}
								tabindex="0"
								role="button"
							>
								<td class="px-4 py-2">
									{#if expandedFindings[finding.id]}
										<ChevronDown class="h-3.5 w-3.5 text-muted-foreground" />
									{:else}
										<ChevronRight class="h-3.5 w-3.5 text-muted-foreground" />
									{/if}
								</td>
								<td class="px-4 py-2">
									<Badge variant="outline" class="text-[10px] {severityColors[finding.severity] ?? ''}">{finding.severity}</Badge>
								</td>
								<td class="px-4 py-2 max-w-xs truncate">{finding.title}</td>
								<td class="px-4 py-2 font-mono text-xs max-w-[200px] truncate">{finding.file_path || '—'}</td>
								<td class="px-4 py-2 font-mono text-xs">{finding.line_number ?? '—'}</td>
								<td class="px-4 py-2 font-mono text-xs">{finding.rule_id || '—'}</td>
								<td class="px-4 py-2">
									{#if finding.confidence}
										<Badge variant="outline" class="text-[10px] {finding.confidence === 'high' ? 'bg-emerald-500/15 text-emerald-500' : finding.confidence === 'medium' ? 'bg-amber-500/15 text-amber-500' : 'bg-zinc-500/15 text-zinc-400'}">{finding.confidence}</Badge>
									{/if}
								</td>
							</tr>
							{#if expandedFindings[finding.id]}
								<tr>
									<td colspan="7" class="bg-muted/20 px-6 py-4">
										<div class="space-y-3 text-sm">
											{#if finding.description}
												<div>
													<h4 class="text-xs font-medium text-muted-foreground mb-1">Description</h4>
													<p>{finding.description}</p>
												</div>
											{/if}
											{#if finding.suggestion}
												<div>
													<h4 class="text-xs font-medium text-muted-foreground mb-1">Suggestion</h4>
													<p class="text-emerald-500">{finding.suggestion}</p>
												</div>
											{/if}
											{#if finding.code_snippet}
												<div>
													<h4 class="text-xs font-medium text-muted-foreground mb-1">Code</h4>
													<pre class="rounded-md bg-muted p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap">{finding.code_snippet}</pre>
												</div>
											{/if}
											<div class="flex flex-wrap gap-3 text-xs text-muted-foreground">
												{#if finding.category}<span>Category: <strong>{finding.category}</strong></span>{/if}
												{#if finding.analyzer_name}<span>Analyzer: <strong>{finding.analyzer_name}</strong></span>{/if}
											</div>
										</div>
									</td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Content>
	</Card.Root>

	{#if pagination && pagination.page < pagination.pages}
		<Button
			variant="outline"
			size="sm"
			onclick={() => onLoadFindings(result, pagination.page + 1)}
			disabled={loading}
		>
			{#if loading}
				<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin" /> Loading…
			{:else}
				Load more ({pagination.total - findings.length} remaining)
			{/if}
		</Button>
	{/if}
{/if}
