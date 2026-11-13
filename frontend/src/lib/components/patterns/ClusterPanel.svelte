<script lang="ts">
	/**
	 * ClusterPanel — variant clusters per target (spec 7.9, 10.3 §4): cluster sizes and shares, whether the
	 * original falls in a cluster, distinct count, top-cluster share, entropy and Chao1 (`low_effective_n` when
	 * exact duplicates dominate), the representative's code with its diff against the original, manual labels
	 * (a `manual` detector coding a cluster key; one label per labeler, never altering past runs) and "Create
	 * detector from selection" on the representative's file.
	 *
	 * Props: `patternRunId`, `summary` (the run's light summary: targets, conditions, detectors).
	 */
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';
	import { toast } from 'svelte-sonner';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getClusters, labelCluster } from '$lib/api/v3/patterns';
	import { getSampleFileContent } from '$lib/api/v3/samples';
	import { getSlotVariantDiff } from '$lib/api/v3/slots';
	import type { ClusterRow, ClusterStats, ClustersOut, PatternSummary, SlotVariantDiffOut } from '$lib/api/v3/types';
	import CodeViewer from '$lib/components/runs/CodeViewer.svelte';
	import { shortSha } from '$lib/components/runs/format';
	import DetectorFromSelection from './DetectorFromSelection.svelte';
	import { fmtClusterStats, fmtProportion } from './format';

	interface Props {
		patternRunId: string;
		summary: PatternSummary;
	}

	let { patternRunId, summary }: Props = $props();

	const targetEntries = $derived(Object.entries(summary.targets ?? {}));
	const conditionEntries = $derived(
		Object.entries(summary.conditions ?? {}).sort(([, a], [, b]) => (a.index ?? 0) - (b.index ?? 0)),
	);
	const manualDetectors = $derived((summary.detectors ?? []).filter((d) => d.engine === 'manual'));

	let target = $state('');
	let condition = $state('');
	let data = $state<ClustersOut | null>(null);
	let loading = $state(false);
	let error = $state('');
	let expanded = $state<number | null>(null);

	$effect(() => {
		if (!target && targetEntries.length) target = targetEntries[0][0];
	});

	$effect(() => {
		if (!target) return;
		const controller = new AbortController();
		loading = true;
		error = '';
		getClusters(patternRunId, { target, condition: condition ? Number(condition) : undefined }, { signal: controller.signal })
			.then((out) => (data = out))
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the clusters.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	// Clusters of several variants, the original's and labelled ones lead; one-variant clusters (distinct
	// implementations nobody else converged on) are counted and opened on demand.
	const CLUSTER_PREVIEW = 8;
	let showAllClusters = $state(false);
	const notable = (c: ClusterRow) => c.size > 1 || c.includes_original || c.labels.length > 0;
	const leadClusters = $derived.by(() => {
		const all = data?.clusters ?? [];
		const head = all.filter(notable);
		return head.length ? head : all.slice(0, CLUSTER_PREVIEW);
	});
	const shownClusters = $derived(showAllClusters ? (data?.clusters ?? []) : leadClusters);
	const hiddenClusters = $derived((data?.clusters.length ?? 0) - leadClusters.length);
	const statsRows = $derived<ClusterStats[]>(data ? (Array.isArray(data.stats) ? data.stats : [data.stats]) : []);
	const targetInfo = $derived(target ? summary.targets?.[target] : undefined);

	// ── representative code, diff, selection → detector ─────────────────────
	let diff = $state<SlotVariantDiffOut | null>(null);
	let fileText = $state<string | null>(null);
	let codeView = $state<'replacement' | 'diff' | 'file'>('replacement');
	let repError = $state('');
	let selected = $state<{ start_line: number; end_line: number } | null>(null);
	let detectorOpen = $state(false);

	async function openCluster(cluster: ClusterRow) {
		if (expanded === cluster.cluster_id) {
			expanded = null;
			return;
		}
		expanded = cluster.cluster_id;
		diff = null;
		fileText = null;
		selected = null;
		repError = '';
		codeView = 'replacement';
		try {
			diff = await getSlotVariantDiff(cluster.representative);
		} catch (e) {
			repError = formatApiError(e, 'Could not load the representative variant.');
		}
	}

	async function showFile(cluster: ClusterRow) {
		codeView = 'file';
		if (fileText !== null || !diff) return;
		try {
			fileText = await getSampleFileContent(cluster.representative, diff.path);
		} catch (e) {
			repError = formatApiError(e, 'Could not load the representative file.');
		}
	}

	// ── manual labels ───────────────────────────────────────────────────────
	let labelDetector = $state('');
	let labelValue = $state<'present' | 'absent' | 'unsure'>('present');
	let labelNote = $state('');
	let labelling = $state(false);

	$effect(() => {
		if (!labelDetector && manualDetectors.length) labelDetector = manualDetectors[0].ref;
	});

	async function saveLabel(cluster: ClusterRow) {
		labelling = true;
		try {
			await labelCluster({ slot_target: cluster.target, normalized_sha: cluster.key, detector: labelDetector, value: labelValue, note: labelNote });
			toast.success('Label saved (it applies to this cluster key in later pattern runs)');
			labelNote = '';
			data = await getClusters(patternRunId, { target, condition: condition ? Number(condition) : undefined });
		} catch (e) {
			toast.error(formatApiError(e, 'Could not save the label.'));
		} finally {
			labelling = false;
		}
	}
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<div class="table-toolbar flex-wrap gap-2">
		<div class="flex flex-wrap items-center gap-2">
			<Select.Simple
				size="sm"
				aria-label="Target"
				bind:value={target}
				items={targetEntries.map(([id, t]) => ({ value: id, label: t.label || id.slice(0, 8), description: `${t.kind} · ${t.path} · parent ${t.parent_model}` }))}
			/>
			<Select.Simple
				size="sm"
				aria-label="Condition"
				bind:value={condition}
				placeholder="All conditions"
				clearLabel="All conditions"
				items={conditionEntries.map(([id, c]) => ({ value: id, label: c.label || `condition ${id}` }))}
			/>
		</div>
		{#if targetInfo}
			<span class="text-xs text-muted-foreground">
				{#if !targetInfo.informative}<Badge variant="secondary">uninformative target</Badge>{/if}
				original {targetInfo.original_normalized_sha ? shortSha(targetInfo.original_normalized_sha, 10) : `not normalised (${targetInfo.original_parse_reason})`}
			</span>
		{/if}
	</div>

	{#if targetEntries.length === 0}
		<EmptyState compact title="No targets in this pattern run" />
	{:else if loading && !data}
		<LoadingState label="loading clusters" />
	{:else if error}
		<div class="p-4"><ErrorAlert message={error} /></div>
	{:else if data}
		{#if statsRows.length}
			<ul class="space-y-0.5 border-b border-border px-4 py-2 text-xs">
				{#each statsRows as s, i (i)}
					<li>
						{#if s.condition !== undefined}<span class="text-muted-foreground">{summary.conditions?.[String(s.condition)]?.label ?? `condition ${s.condition}`}:</span>{/if}
						{fmtClusterStats(s)}
						{#if s.low_effective_n}<Badge variant="secondary" class="ml-1" title="More than half the variants are exact duplicates">low effective n</Badge>{/if}
					</li>
				{/each}
			</ul>
		{/if}
		{#if data.clusters.length === 0}
			<EmptyState compact title="No clusters" description="No variant of this target produced normalisable output." />
		{:else}
			<ul class="divide-y divide-border">
				{#each shownClusters as cluster (cluster.cluster_id)}
					<li class="px-4 py-2.5">
						<button type="button" class="flex w-full cursor-pointer flex-wrap items-center gap-3 text-left text-sm" aria-expanded={expanded === cluster.cluster_id} onclick={() => openCluster(cluster)}>
							{#if expanded === cluster.cluster_id}<ChevronDown class="h-4 w-4" />{:else}<ChevronRight class="h-4 w-4" />{/if}
							<span class="font-mono text-xs">#{cluster.cluster_id}</span>
							<span class="relative h-2 w-32 rounded-sm bg-muted" aria-hidden="true">
								<span class="absolute inset-y-0 left-0 rounded-sm bg-[color:var(--chart-1)]" style={`width:${Math.max(1, (cluster.share ?? 0) * 100)}%`}></span>
							</span>
							<span class="tabular-nums">{fmtProportion(cluster.share)} · {cluster.size} variant{cluster.size === 1 ? '' : 's'}</span>
							<span class="text-xs text-muted-foreground">{cluster.method}</span>
							{#if cluster.includes_original}<Badge variant="outline" title="The original part normalises into this cluster">includes the original</Badge>{/if}
							{#each cluster.labels as label, li (li)}
								<Badge variant="secondary" title={label.note ?? ''}>{label.feature_key ?? 'label'}: {label.value}{label.labeler ? ` (${label.labeler})` : ''}</Badge>
							{/each}
						</button>
						{#if expanded === cluster.cluster_id}
							<div class="mt-2 space-y-2">
								{#if repError}<ErrorAlert message={repError} />{/if}
								<p class="text-xs text-muted-foreground">
									Representative <a class="font-mono text-primary hover:underline" href={`/samples/${cluster.representative}`}>{shortSha(cluster.representative, 8)}</a>
									· members {#each cluster.members_sample as m, mi (m)}{mi ? ', ' : ''}<a class="font-mono text-primary hover:underline" href={`/samples/${m}`}>{shortSha(m, 6)}</a>{/each}
									{cluster.size > cluster.members_sample.length ? '…' : ''}
								</p>
								{#if diff}
									<div class="flex flex-wrap items-center gap-2">
										<Segmented
											size="sm"
											aria-label="Representative view"
											value={codeView}
											onValueChange={(v) => (v === 'file' ? showFile(cluster) : (codeView = v as typeof codeView))}
											items={[
												{ value: 'replacement', label: 'Replacement' },
												{ value: 'diff', label: 'Diff vs original' },
												{ value: 'file', label: 'File (select lines)' },
											]}
										/>
										{#if codeView === 'file'}
											<Button variant="outline" size="sm" disabled={!selected} onclick={() => (detectorOpen = true)} title={selected ? undefined : 'Drag over line numbers first'}>
												<WandSparkles class="mr-1.5 h-3.5 w-3.5" />Create detector from selection
											</Button>
										{/if}
									</div>
									{#if codeView === 'replacement'}
										<CodeViewer code={diff.replacement} path={diff.path} class="max-h-80" />
									{:else if codeView === 'diff'}
										<CodeViewer code={diff.unified || '(identical to the original)'} language="diff" class="max-h-80" />
									{:else if fileText !== null}
										<CodeViewer
											code={fileText}
											path={diff.path}
											selectable
											highlight={selected}
											onselectlines={(start, end, done) => {
												if (done) selected = { start_line: start, end_line: end };
											}}
											class="max-h-96"
										/>
									{:else}
										<LoadingState label="loading file" class="py-6" />
									{/if}
									<DetectorFromSelection bind:open={detectorOpen} runId={cluster.representative} path={diff.path} span={selected} language={targetInfo?.language} />
								{:else if !repError}
									<LoadingState label="loading representative" class="py-6" />
								{/if}

								<div class="flex flex-wrap items-end gap-2 rounded-md border border-border p-2 text-xs">
									<span class="section-label w-full">Manual label for this cluster key</span>
									{#if manualDetectors.length === 0}
										<p class="text-muted-foreground">
											Add a detector with engine <span class="font-mono">manual</span> to this run's detector set (Library → Detectors) to code clusters by hand.
										</p>
									{:else}
										<Select.Simple size="sm" aria-label="Manual detector" bind:value={labelDetector} items={manualDetectors.map((d) => ({ value: d.ref, label: d.feature_key, description: d.ref }))} />
										<Segmented
											size="sm"
											aria-label="Label"
											value={labelValue}
											onValueChange={(v) => (labelValue = v as typeof labelValue)}
											items={[
												{ value: 'present', label: 'Present' },
												{ value: 'absent', label: 'Absent' },
												{ value: 'unsure', label: 'Unsure' },
											]}
										/>
										<Input class="h-8 w-56" placeholder="Note (optional)" aria-label="Note" bind:value={labelNote} />
										<Button size="sm" disabled={labelling || !labelDetector} onclick={() => saveLabel(cluster)}>Save label</Button>
									{/if}
								</div>
							</div>
						{/if}
					</li>
				{/each}
			</ul>
			{#if hiddenClusters > 0}
				<button type="button" class="w-full border-t border-border py-2 text-xs font-medium text-muted-foreground hover:bg-surface-2 hover:text-foreground" aria-expanded={showAllClusters} onclick={() => (showAllClusters = !showAllClusters)}>
					{showAllClusters ? 'Show fewer clusters' : `Show ${hiddenClusters} more clusters (one variant each)`}
				</button>
			{/if}
		{/if}
	{/if}
</Card.Root>
