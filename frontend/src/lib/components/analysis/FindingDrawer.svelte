<script lang="ts">
	/**
	 * FindingDrawer — one finding in a side sheet: severity, tool and rule,
	 * location and scope, whether (and why not) it counts, CWE, origin,
	 * description and fix, AI grounding / consensus / sample, the code around
	 * the line (re-resolved, sha-verified file), fingerprint and issue key,
	 * and the raw tool data.
	 *
	 * Props:
	 * - `open` (bindable), `finding`, `runId`.
	 * - `position`: `{ index, total }` of the finding in the current list;
	 *   `onprev()` / `onnext()` step through it (also ← / → keys).
	 * - `onissue(finding)`: "All findings of this issue" (filters the list).
	 * - `onfile(path)`: open the file in the Files tab.
	 */
	import type { Finding } from '$lib/api/analysis';
	import * as Sheet from '$lib/components/ui/sheet';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { groundingTones, severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import CopyValue from './CopyValue.svelte';
	import RunFileViewer from './RunFileViewer.svelte';
	import {
		categoryLabel,
		cweUrl,
		fmtDateTime,
		fmtLocation,
		tsd,
		uncountedReason
	} from './run-utils';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleMinus from '@lucide/svelte/icons/circle-minus';
	import FileCode from '@lucide/svelte/icons/file-code';
	import Layers from '@lucide/svelte/icons/layers';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		open?: boolean;
		finding: Finding | null;
		runId: string;
		position?: { index: number; total: number } | null;
		onprev?: () => void;
		onnext?: () => void;
		onissue?: (finding: Finding) => void;
		onfile?: (path: string) => void;
	}

	let {
		open = $bindable(false),
		finding,
		runId,
		position = null,
		onprev,
		onnext,
		onissue,
		onfile
	}: Props = $props();

	const hasPrev = $derived(!!onprev && !!position && position.index > 0);
	const hasNext = $derived(!!onnext && !!position && position.index < position.total - 1);

	const GROUNDING_TEXT: Record<string, string> = {
		exact: 'The quoted evidence was found within ±3 lines of the cited range.',
		relocated:
			'The quoted evidence was found elsewhere in the file; the finding was moved to that line.',
		ungrounded: 'The quoted evidence was not found in the file: the finding cannot be counted.',
		invalid_file: 'The cited file is not part of the reviewed code: the finding cannot be counted.',
		out_of_range: 'The cited lines are outside the file: the finding cannot be counted.'
	};

	const citedLines = $derived(finding ? (tsd<number[]>(finding, 'cited_lines') ?? null) : null);
	const aiConfidence = $derived(finding ? tsd<number>(finding, 'confidence') : undefined);
	const reviewer = $derived(
		finding
			? tsd<{ model?: string; engine?: string; harness?: string }>(finding, 'reviewer')
			: undefined
	);
	const rawData = $derived(
		finding && Object.keys(finding.tool_specific_data ?? {}).length > 0
			? JSON.stringify(finding.tool_specific_data, null, 2)
			: ''
	);

	function onkeydown(event: KeyboardEvent) {
		if (!open) return;
		const target = event.target as HTMLElement | null;
		if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
		if (event.key === 'ArrowLeft' && hasPrev) {
			event.preventDefault();
			onprev?.();
		} else if (event.key === 'ArrowRight' && hasNext) {
			event.preventDefault();
			onnext?.();
		}
	}
</script>

<svelte:window {onkeydown} />

<Sheet.Root bind:open>
	<Sheet.Content
		side="right"
		showClose={false}
		class="w-full gap-0 overflow-y-auto p-0 sm:max-w-xl lg:max-w-2xl"
	>
		{#if finding}
			<div
				class="sticky top-0 z-10 space-y-2 border-b border-border bg-background px-4 py-3 sm:px-5"
			>
				<div class="flex flex-wrap items-center gap-1.5">
					<Badge class={severityColors[finding.severity]}>{finding.severity}</Badge>
					{#if finding.is_ai}
						<Badge variant="outline" class="gap-1">
							<Sparkles class="size-3 text-accent-3" aria-hidden="true" />
							AI review{#if finding.review_sample_index != null}&nbsp;· sample #{finding.review_sample_index}{/if}
						</Badge>
					{:else}
						<Badge variant="outline" class="gap-1">
							<ScanSearch class="size-3 text-accent-2" aria-hidden="true" />
							Static
						</Badge>
					{/if}
					{#if finding.counted}
						<Badge variant="outline" class="gap-1 border-success/40 text-success-fg">
							<CircleCheck class="size-3" aria-hidden="true" />Counted
						</Badge>
					{:else}
						<Badge variant="secondary" class="gap-1">
							<CircleMinus class="size-3" aria-hidden="true" />Not counted
						</Badge>
					{/if}
					<span class="ml-auto flex items-center gap-1">
						{#if position}
							<span class="text-[11px] text-muted-foreground tabular-nums">
								{position.index + 1} / {position.total}
							</span>
							<Button
								variant="ghost"
								size="icon"
								class="size-7"
								disabled={!hasPrev}
								onclick={() => onprev?.()}
								aria-label="Previous finding"
							>
								<ChevronLeft class="size-4" />
							</Button>
							<Button
								variant="ghost"
								size="icon"
								class="size-7"
								disabled={!hasNext}
								onclick={() => onnext?.()}
								aria-label="Next finding"
							>
								<ChevronRight class="size-4" />
							</Button>
						{/if}
						<Sheet.Close
							class="inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
						>
							<X class="size-4" aria-hidden="true" />
							<span class="sr-only">Close</span>
						</Sheet.Close>
					</span>
				</div>
				<Sheet.Title class="text-base font-semibold leading-snug break-words">
					{finding.title || finding.rule_id || 'Finding'}
				</Sheet.Title>
				<Sheet.Description
					class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted-foreground"
				>
					<span class="font-mono">{finding.tool_slug}</span>
					{#if finding.rule_id}
						<span aria-hidden="true">·</span>
						<span class="break-all font-mono">{finding.rule_id}</span>
					{/if}
				</Sheet.Description>
			</div>

			<div class="space-y-5 px-4 py-4 sm:px-5">
				{#if !finding.counted}
					<p
						class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
					>
						<span class="font-medium text-foreground">Why it does not count:</span>
						{uncountedReason(finding)}
					</p>
				{/if}

				<section aria-labelledby="fd-where" class="space-y-2">
					<h3 id="fd-where" class="section-label">Where</h3>
					<div class="flex flex-wrap items-center gap-2">
						<code class="break-all text-xs">
							{fmtLocation(
								finding.file_path,
								finding.line_number,
								finding.end_line
							)}{finding.column_number ? `:${finding.column_number}` : ''}
						</code>
						{#if onfile && finding.file_path}
							<Button
								variant="ghost"
								size="sm"
								class="h-6 px-2"
								onclick={() => onfile(finding.file_path)}
							>
								<FileCode class="size-3.5" />Open in Files
							</Button>
						{/if}
					</div>
					<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Origin</dt>
						<dd>{finding.file_origin}</dd>
						<dt class="text-muted-foreground">Measured scope</dt>
						<dd>
							{finding.in_measured_scope
								? 'In scope (code the model wrote)'
								: 'Outside the measured scope'}
						</dd>
						<dt class="text-muted-foreground">Category</dt>
						<dd>{categoryLabel(finding.category)}</dd>
						{#if finding.cwe.length > 0}
							<dt class="text-muted-foreground">CWE</dt>
							<dd class="flex flex-wrap gap-1.5">
								{#each finding.cwe as cwe (cwe)}
									{@const url = cweUrl(cwe)}
									{#if url}
										<a
											href={url}
											target="_blank"
											rel="noopener noreferrer"
											class="font-mono text-primary underline-offset-2 hover:underline">{cwe}</a
										>
									{:else}
										<span class="font-mono">{cwe}</span>
									{/if}
								{/each}
							</dd>
						{/if}
						<dt class="text-muted-foreground">Confidence</dt>
						<dd>
							{finding.confidence || '—'}{#if aiConfidence != null}
								<span class="text-muted-foreground">{' '}(model: {aiConfidence.toFixed(2)})</span
								>{/if}
						</dd>
						{#if finding.native_severity}
							<dt class="text-muted-foreground">Tool severity</dt>
							<dd class="font-mono">{finding.native_severity}</dd>
						{/if}
					</dl>
				</section>

				{#if finding.is_ai}
					<section aria-labelledby="fd-ai" class="space-y-2">
						<h3 id="fd-ai" class="section-label">AI evidence</h3>
						<div class="flex flex-wrap items-center gap-2 text-xs">
							<StatusBadge
								status={finding.grounding || 'unknown'}
								tones={groundingTones}
								label={finding.grounding
									? `grounding: ${finding.grounding.replace(/_/g, ' ')}`
									: 'grounding unknown'}
							/>
							<StatusBadge
								status={finding.is_consensus ? 'consensus' : 'no consensus'}
								tone={finding.is_consensus ? 'success' : 'neutral'}
							/>
						</div>
						{#if finding.grounding && GROUNDING_TEXT[finding.grounding]}
							<p class="text-xs text-muted-foreground">
								{GROUNDING_TEXT[finding.grounding]}
							</p>
						{/if}
						<dl class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
							{#if citedLines && citedLines.length > 0}
								<dt class="text-muted-foreground">Cited lines</dt>
								<dd class="tabular-nums">
									{citedLines[0]}{citedLines[1] && citedLines[1] !== citedLines[0]
										? `–${citedLines[1]}`
										: ''}
									{#if finding.grounding === 'relocated' && finding.line_number}
										<span class="text-muted-foreground">→ moved to {finding.line_number}</span>
									{/if}
								</dd>
							{/if}
							{#if reviewer?.model}
								<dt class="text-muted-foreground">Reviewer</dt>
								<dd class="break-all">
									<span class="font-mono">{reviewer.model}</span>
									<span class="text-muted-foreground">
										· {reviewer.engine ?? '—'}{reviewer.harness
											? ` · ${reviewer.harness}`
											: ''}</span
									>
								</dd>
							{/if}
						</dl>
					</section>
				{/if}

				{#if finding.description}
					<section aria-labelledby="fd-desc" class="space-y-1">
						<h3 id="fd-desc" class="section-label">Description</h3>
						<p class="whitespace-pre-line break-words text-sm">
							{finding.description}
						</p>
					</section>
				{/if}
				{#if finding.suggestion}
					<section aria-labelledby="fd-fix" class="space-y-1">
						<h3 id="fd-fix" class="section-label">Suggested fix</h3>
						<p class="whitespace-pre-line break-words text-sm">
							{finding.suggestion}
						</p>
					</section>
				{/if}

				{#if finding.file_path}
					<section aria-labelledby="fd-code" class="space-y-2">
						<h3 id="fd-code" class="section-label">Code</h3>
						<RunFileViewer
							{runId}
							path={finding.file_path}
							highlight={finding.line_number
								? { start: finding.line_number, end: finding.end_line }
								: null}
							context={finding.line_number ? 8 : null}
							maxHeight="max-h-[24rem]"
							fallbackSnippet={finding.code_snippet}
							fallbackLine={finding.line_number}
						/>
					</section>
				{/if}

				<section aria-labelledby="fd-id" class="space-y-2">
					<h3 id="fd-id" class="section-label">Identity</h3>
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-1.5 text-xs">
						<dt class="text-muted-foreground">Fingerprint</dt>
						<dd class="min-w-0">
							<CopyValue
								value={finding.fingerprint}
								display={finding.fingerprint.slice(0, 16)}
								label="fingerprint"
							/>
						</dd>
						<dt class="text-muted-foreground">Issue key</dt>
						<dd class="flex min-w-0 flex-wrap items-center gap-2">
							{#if finding.issue_key}
								<CopyValue
									value={finding.issue_key}
									display={finding.issue_key.slice(0, 16)}
									label="issue key"
								/>
								{#if onissue}
									<Button
										variant="ghost"
										size="sm"
										class="h-6 px-2"
										onclick={() => onissue(finding)}
									>
										<Layers class="size-3.5" />All findings of this issue
									</Button>
								{/if}
							{:else}
								<span class="text-muted-foreground"
									>Not clustered (only counted findings are deduplicated)</span
								>
							{/if}
						</dd>
						<dt class="text-muted-foreground">Finding id</dt>
						<dd class="min-w-0">
							<CopyValue value={finding.id} display={finding.id.slice(0, 8)} label="finding id" />
						</dd>
						<dt class="text-muted-foreground">Recorded</dt>
						<dd>{fmtDateTime(finding.created_at)}</dd>
					</dl>
				</section>

				{#if rawData}
					<details class="text-xs">
						<summary
							class="cursor-pointer select-none font-medium text-muted-foreground hover:text-foreground"
						>
							Raw tool data
						</summary>
						<pre class={cn('terminal mt-2 max-h-72')}>{rawData}</pre>
					</details>
				{/if}
			</div>
		{/if}
	</Sheet.Content>
</Sheet.Root>
