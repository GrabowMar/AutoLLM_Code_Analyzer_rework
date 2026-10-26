<script lang="ts">
	/**
	 * RunFiles — the analysed sample: every file with its scope decision
	 * (included / measured / excluded and why), origin, language, SLOC and
	 * finding counts, and a code viewer with per-line finding markers.
	 * The selected file is in the URL (`path`).
	 *
	 * Props: `run`, `revision`, `onfindings(path)` (findings of one file).
	 */
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import type { AnalysisRunDetail, Finding, RunFile, RunFiles, Severity } from '$lib/api/analysis';
	import { getRunFiles, listRunFindings, SEVERITIES } from '$lib/api/analysis';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import { severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import type { LineMarker } from '../CodeViewer.svelte';
	import CopyValue from '../CopyValue.svelte';
	import FindingDrawer from '../FindingDrawer.svelte';
	import RunFileViewer from '../RunFileViewer.svelte';
	import { fmtBytes, fmtInt } from '../run-utils';
	import { replaceParams } from './url';
	import FileCode from '@lucide/svelte/icons/file-code';
	import FolderOpen from '@lucide/svelte/icons/folder-open';
	import ListFilter from '@lucide/svelte/icons/list-filter';
	import Search from '@lucide/svelte/icons/search';

	interface Props {
		run: AnalysisRunDetail;
		revision: string;
		onfindings?: (path: string) => void;
	}

	let { run, revision, onfindings }: Props = $props();

	let data = $state<RunFiles | null>(null);
	let loading = $state(true);
	let error = $state('');
	let controller: AbortController | null = null;

	async function load(silent: boolean) {
		controller?.abort();
		const mine = new AbortController();
		controller = mine;
		try {
			data = await getRunFiles(run.id, {}, { signal: mine.signal });
			error = '';
		} catch (e) {
			if (isAbortError(e)) return;
			if (!(silent && data)) error = formatApiError(e, 'Could not load the sample files.');
		} finally {
			if (controller === mine) loading = false;
		}
	}

	let loadedOnce = false;
	$effect(() => {
		void revision;
		untrack(() => {
			void load(loadedOnce);
			loadedOnce = true;
		});
		return () => controller?.abort();
	});

	// ── List filters (client side: the manifest is loaded whole) ───────
	let search = $state('');
	let scope = $state<'all' | 'measured' | 'excluded'>('all');

	const files = $derived(data?.files ?? []);
	const visible = $derived(
		files.filter((f) => {
			if (scope === 'measured' && !f.measured) return false;
			if (scope === 'excluded' && f.included) return false;
			if (search && !f.path.toLowerCase().includes(search.trim().toLowerCase())) return false;
			return true;
		})
	);

	const selectedPath = $derived(page.url.searchParams.get('path') ?? '');
	const selected = $derived<RunFile | null>(
		files.find((f) => f.path === selectedPath) ??
			(selectedPath
				? null
				: (files.find((f) => f.measured && f.findings.total > 0) ??
					files.find((f) => f.included) ??
					null))
	);

	function select(path: string) {
		void replaceParams(page.url, { path });
	}

	// ── Findings of the selected file → gutter markers ─────────────────
	let fileFindings = $state<Finding[]>([]);
	let findingsFor = $state('');
	let findingsController: AbortController | null = null;

	$effect(() => {
		const path = selected?.path ?? '';
		void revision;
		if (!path || !selected?.included) {
			fileFindings = [];
			findingsFor = path;
			return;
		}
		untrack(() => {
			findingsController?.abort();
			const mine = new AbortController();
			findingsController = mine;
			listRunFindings(run.id, { file: path, per_page: 200, facets: false }, { signal: mine.signal })
				.then((res) => {
					// Static findings, plus the AI findings that count (consensus representatives).
					fileFindings = res.items.filter((f) => !f.is_ai || f.counted);
					findingsFor = path;
				})
				.catch((e) => {
					if (!isAbortError(e)) {
						fileFindings = [];
						findingsFor = path;
					}
				});
		});
		return () => findingsController?.abort();
	});

	const markers = $derived.by(() => {
		const out: Record<number, LineMarker> = {};
		const byLine = new Map<number, Finding[]>();
		for (const f of fileFindings) {
			if (!f.line_number) continue;
			const list = byLine.get(f.line_number) ?? [];
			list.push(f);
			byLine.set(f.line_number, list);
		}
		for (const [line, list] of byLine) {
			const worst = list.reduce<Severity>(
				(acc, f) => (SEVERITIES.indexOf(f.severity) < SEVERITIES.indexOf(acc) ? f.severity : acc),
				'info'
			);
			out[line] = {
				severity: worst,
				count: list.length,
				label: `${list.length} finding${list.length === 1 ? '' : 's'}: ${[...new Set(list.map((f) => f.rule_id || f.tool_slug))].join(', ')}`
			};
		}
		return out;
	});

	let activeLine = $state<number | null>(null);
	$effect(() => {
		void selectedPath;
		activeLine = null;
	});

	const lineFindings = $derived(
		activeLine == null ? [] : fileFindings.filter((f) => f.line_number === activeLine)
	);

	let drawerOpen = $state(false);
	let drawerIndex = $state(-1);
	const drawerFinding = $derived(drawerIndex >= 0 ? (lineFindings[drawerIndex] ?? null) : null);

	const totals = $derived(data?.totals ?? {});
</script>

{#if loading && !data}
	<LoadingState label="loading sample files" />
{:else if error && !data}
	<ErrorAlert message={error} onretry={() => load(false)} />
{:else if data && files.length === 0}
	<EmptyState
		icon={FolderOpen}
		title="No files in this sample"
		description="The resolver found no files to analyse."
	/>
{:else if data}
	<div class="space-y-4">
		<div class="flex flex-wrap gap-x-6 gap-y-1 text-xs text-muted-foreground">
			<span
				><span class="font-medium text-foreground tabular-nums"
					>{fmtInt(totals.files ?? data.count)}</span
				> files</span
			>
			<span
				><span class="font-medium text-foreground tabular-nums">{fmtInt(totals.included)}</span> included</span
			>
			<span
				><span class="font-medium text-foreground tabular-nums">{fmtInt(totals.measured)}</span> measured</span
			>
			<span>
				<span class="font-medium text-foreground tabular-nums">{fmtInt(totals.measured_sloc)}</span>
				of
				{fmtInt(totals.sloc)} SLOC measured
			</span>
			<span
				><span class="font-medium text-foreground tabular-nums"
					>{fmtInt(totals.suppression_markers)}</span
				> suppression markers</span
			>
			<span
				>source <span class="font-medium text-foreground">{data.source}</span> · {data.resolver_version}
				· {data.sloc_version}</span
			>
		</div>
		{#if data.warnings.length > 0}
			<ul class="list-disc space-y-0.5 pl-4 text-xs text-warning-fg">
				{#each data.warnings as w, i (i)}<li>{w}</li>{/each}
			</ul>
		{/if}

		<div
			class="grid gap-4 lg:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)]"
		>
			<Card.Root class="gap-0 py-0">
				<div class="space-y-2 border-b border-border p-3">
					<div class="relative">
						<Search
							class="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
							aria-hidden="true"
						/>
						<Input
							bind:value={search}
							placeholder="Filter by path…"
							class="h-8 pl-8 text-xs"
							aria-label="Filter files by path"
						/>
					</div>
					<Segmented
						size="sm"
						fullWidth
						aria-label="File scope"
						bind:value={scope}
						items={[
							{ value: 'all', label: `All (${files.length})` },
							{ value: 'measured', label: 'Measured' },
							{ value: 'excluded', label: 'Excluded' }
						]}
					/>
				</div>
				<ul class="max-h-[32rem] overflow-y-auto py-1" aria-label="Sample files">
					{#each visible as f (f.path)}
						{@const isSel = selected?.path === f.path}
						<li>
							<button
								type="button"
								class={cn(
									'flex w-full cursor-pointer flex-col gap-0.5 border-l-2 px-3 py-1.5 text-left transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none',
									isSel ? 'border-primary bg-primary/5' : 'border-transparent',
									!f.included && 'opacity-70'
								)}
								aria-current={isSel ? 'true' : undefined}
								onclick={() => select(f.path)}
							>
								<span class="truncate font-mono text-xs" title={f.path}>{f.path}</span>
								<span class="flex flex-wrap items-center gap-x-2 text-[11px] text-muted-foreground">
									<span>{f.language || f.kind}</span>
									<span>{f.origin}</span>
									{#if f.included}
										<span class="tabular-nums"
											>{f.measured ? `${f.measured_sloc} SLOC` : 'not measured'}</span
										>
									{:else}
										<span class="text-warning-fg"
											>excluded: {f.exclude_reason.replace(/_/g, ' ')}</span
										>
									{/if}
									{#if f.findings.total > 0}
										<span class="ml-auto tabular-nums text-foreground">
											{f.findings.counted}<span class="text-muted-foreground"
												>/{f.findings.total}</span
											>
											{#if f.findings.ai_consensus > 0}<span class="text-accent-3">
													· {f.findings.ai_consensus} AI</span
												>{/if}
										</span>
									{/if}
								</span>
							</button>
						</li>
					{:else}
						<li class="px-3 py-6 text-center text-xs text-muted-foreground">No file matches.</li>
					{/each}
				</ul>
			</Card.Root>

			<div class="min-w-0 space-y-3">
				{#if !selected}
					<EmptyState
						icon={FileCode}
						title={selectedPath ? 'This file is not part of the sample' : 'Select a file'}
						description={selectedPath
							? selectedPath
							: 'Pick a file on the left to read it with its findings.'}
					/>
				{:else}
					<div class="flex flex-wrap items-start justify-between gap-2">
						<div class="min-w-0 space-y-1">
							<p class="break-all font-mono text-sm font-medium">
								{selected.path}
							</p>
							<div class="flex flex-wrap items-center gap-1.5">
								<Badge variant="outline">{selected.language || 'unknown language'}</Badge>
								<Badge variant="outline">{selected.kind}</Badge>
								<Badge variant="outline">origin: {selected.origin}</Badge>
								{#if !selected.included}
									<Badge variant="secondary"
										>excluded: {selected.exclude_reason.replace(/_/g, ' ')}</Badge
									>
								{:else if selected.measured}
									<Badge class="border-success/30 bg-success/10 text-success-fg"
										>measured · {selected.measured_sloc} SLOC</Badge
									>
								{:else}
									<Badge variant="secondary">included, not measured</Badge>
								{/if}
								{#if selected.changed_lines != null}
									<Badge variant="outline"
										>{selected.changed_lines} lines changed by the model</Badge
									>
								{/if}
								{#if selected.suppression_markers > 0}
									<Badge class="border-warning/30 bg-warning/10 text-warning-fg"
										>{selected.suppression_markers} suppression markers</Badge
									>
								{/if}
							</div>
							<div class="flex flex-wrap items-center gap-x-3 text-[11px] text-muted-foreground">
								<span>{fmtBytes(selected.size)} · {selected.sloc} SLOC</span>
								<CopyValue
									value={selected.sha256}
									display="sha256 {selected.sha256.slice(0, 12)}"
									label="file sha256"
								/>
							</div>
						</div>
						{#if onfindings && selected.findings.total > 0}
							<Button variant="outline" size="sm" onclick={() => onfindings(selected!.path)}>
								<ListFilter class="size-3.5" />Findings ({selected.findings.total})
							</Button>
						{/if}
					</div>

					{#if selected.included}
						<RunFileViewer
							runId={run.id}
							path={selected.path}
							markers={findingsFor === selected.path ? markers : {}}
							highlight={activeLine != null ? { start: activeLine } : null}
							maxHeight="max-h-[36rem]"
							onlineclick={(line) => (activeLine = markers[line] ? line : null)}
						/>
						{#if Object.keys(markers).length > 0}
							<p class="text-[11px] text-muted-foreground">
								Dots mark lines with static findings or counted AI consensus; select a line number
								to list them.
							</p>
						{/if}
					{:else}
						<p
							class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground"
						>
							Excluded files are not handed to the tools ({selected.exclude_reason.replace(
								/_/g,
								' '
							)}).
						</p>
						<RunFileViewer runId={run.id} path={selected.path} maxHeight="max-h-[36rem]" />
					{/if}

					{#if activeLine != null && lineFindings.length > 0}
						<Card.Root class="gap-2 py-3">
							<Card.Header>
								<Card.Title class="text-sm">Line {activeLine}</Card.Title>
							</Card.Header>
							<Card.Content>
								<ul class="space-y-1">
									{#each lineFindings as f, i (f.id)}
										<li>
											<button
												type="button"
												class="flex w-full cursor-pointer flex-wrap items-center gap-2 rounded px-2 py-1 text-left text-sm hover:bg-muted/60"
												onclick={() => {
													drawerIndex = i;
													drawerOpen = true;
												}}
											>
												<Badge class={severityColors[f.severity]}>{f.severity}</Badge>
												<span class="min-w-0 flex-1 truncate">{f.title || f.rule_id}</span>
												<span class="font-mono text-[11px] text-muted-foreground"
													>{f.is_ai ? 'AI consensus' : f.tool_slug}</span
												>
											</button>
										</li>
									{/each}
								</ul>
							</Card.Content>
						</Card.Root>
					{/if}
				{/if}
			</div>
		</div>
	</div>

	<FindingDrawer
		bind:open={drawerOpen}
		finding={drawerFinding}
		runId={run.id}
		position={drawerIndex >= 0 ? { index: drawerIndex, total: lineFindings.length } : null}
		onprev={() => (drawerIndex = Math.max(0, drawerIndex - 1))}
		onnext={() => (drawerIndex = Math.min(lineFindings.length - 1, drawerIndex + 1))}
	/>
{/if}
