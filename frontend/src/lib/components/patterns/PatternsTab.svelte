<script lang="ts">
	/**
	 * PatternsTab — the Patterns section of a slot study's record (spec 7.9, 10.3 §4; mounted by WP-15's
	 * `/experiments/[id]`). A PatternRun selector labelled planned (matches the pre-registered pattern plan) or
	 * exploratory, with the live provisional run listed apart; "Re-run patterns (no LLM cost)" queues a new
	 * PatternRun over the existing variants; export zip. Sections: prevalence (ITT and measured n, Wilson CIs,
	 * audit), comparisons (paired / independent; q-values in exploratory), clusters per target, the
	 * variants × features matrix, and detector audits.
	 *
	 * Props (spec 10.8 contract): `{ studyId: string }`.
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import Download from '@lucide/svelte/icons/download';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import ScanSearch from '@lucide/svelte/icons/scan-search';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { toast } from 'svelte-sonner';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import {
		createDetectorAudit,
		createPatternRun,
		getPatternRun,
		listDetectorAudits,
		listPatternRuns,
		patternExportUrl,
	} from '$lib/api/v3/patterns';
	import type { DetectorAuditOut, PatternRunOut, PatternSummary } from '$lib/api/v3/types';
	import { shortSha } from '$lib/components/runs/format';
	import ClusterPanel from './ClusterPanel.svelte';
	import ComparisonTable from './ComparisonTable.svelte';
	import FeatureMatrix from './FeatureMatrix.svelte';
	import PrevalenceTable from './PrevalenceTable.svelte';
	import { fmtProportion } from './format';

	interface Props {
		studyId: string;
	}

	let { studyId }: Props = $props();

	let runs = $state<PatternRunOut[]>([]);
	let loading = $state(true);
	let error = $state('');
	let selectedId = $state('');
	let detail = $state<PatternRunOut | null>(null);
	let audits = $state<DetectorAuditOut[]>([]);
	let rerunning = $state(false);
	let matrixTarget = $state('');

	async function loadRuns(signal?: AbortSignal) {
		loading = untrack(() => runs.length === 0);
		error = '';
		try {
			runs = await listPatternRuns(studyId, undefined, { signal });
			if (!selectedId || !runs.some((r) => r.id === selectedId)) selectedId = defaultRun(runs)?.id ?? '';
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the pattern runs.');
		} finally {
			loading = false;
		}
	}

	/** Newest completed planned run, else newest completed run, else the live run, else the newest. */
	function defaultRun(list: PatternRunOut[]): PatternRunOut | undefined {
		const done = list.filter((r) => !r.is_live && r.status === 'completed');
		return done.find((r) => r.label === 'planned') ?? done[0] ?? list.find((r) => r.is_live) ?? list[0];
	}

	$effect(() => {
		void studyId;
		const controller = new AbortController();
		void loadRuns(controller.signal);
		return () => controller.abort();
	});

	$effect(() => {
		const id = selectedId;
		if (!id) {
			detail = null;
			return;
		}
		const controller = new AbortController();
		getPatternRun(id, { signal: controller.signal })
			.then((out) => (detail = out))
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the pattern run.');
			});
		listDetectorAudits(id, { signal: controller.signal })
			.then((out) => (audits = out))
			.catch(() => (audits = []));
		return () => controller.abort();
	});

	// Poll while the selected run is queued or running.
	const active = $derived(!!detail && !detail.is_live && (detail.status === 'pending' || detail.status === 'running'));
	$effect(() => {
		if (!active) return;
		const id = selectedId;
		const timer = setInterval(async () => {
			try {
				detail = await getPatternRun(id);
				if (detail.status === 'completed' || detail.status === 'failed') await loadRuns();
			} catch {
				// next tick
			}
		}, 8_000);
		return () => clearInterval(timer);
	});

	const summary = $derived((detail?.summary ?? {}) as PatternSummary);
	const ready = $derived(!!detail && (detail.is_live || detail.status === 'completed'));
	const conditionLabels = $derived(
		Object.fromEntries(Object.entries(summary.conditions ?? {}).map(([id, c]) => [id, c.label || `condition ${id}`])),
	);
	const targetLabels = $derived(
		Object.fromEntries(Object.entries(summary.targets ?? {}).map(([id, t]) => [id, `${t.label || id.slice(0, 8)} (${t.parent_model})`])),
	);
	const targetIds = $derived(Object.keys(summary.targets ?? {}));
	$effect(() => {
		if (!matrixTarget || !targetIds.includes(matrixTarget)) matrixTarget = targetIds[0] ?? '';
	});

	async function rerun() {
		rerunning = true;
		try {
			const created = await createPatternRun({ study: studyId, detectors: 'default' });
			toast.success(`Pattern run queued (${created.label}); it reuses the variants, no LLM calls`);
			await loadRuns();
			selectedId = created.id;
		} catch (e) {
			toast.error(formatApiError(e, 'Could not queue a pattern run.'));
		} finally {
			rerunning = false;
		}
	}

	async function audit(featureKey: string) {
		if (!detail) return;
		try {
			const created = await createDetectorAudit(detail.id, { feature_key: featureKey, n_hits: 20, n_non_hits: 20 });
			toast.success(`Audit of ${featureKey} created: ${created.sample.hits.length} hits + ${created.sample.non_hits.length} non-hits to code in Human eval`);
			audits = await listDetectorAudits(detail.id);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not create the audit.'));
		}
	}

	function runLabel(r: PatternRunOut): string {
		if (r.is_live) return 'Live (provisional)';
		const when = r.completed_at ?? r.created_at ?? '';
		return `${r.label} · ${r.status} · ${when.slice(0, 16).replace('T', ' ')}`;
	}
</script>

{#if loading}
	<LoadingState label="loading patterns" />
{:else if error && runs.length === 0}
	<ErrorAlert message={error} onretry={() => loadRuns()} />
{:else if runs.length === 0}
	<EmptyState
		icon={ScanSearch}
		title="No pattern run yet"
		description="Slot experiments run their pattern analysis after the variants are evaluated. You can also start one now: it reuses the variants (no LLM cost)."
	>
		{#snippet action()}
			<Button size="sm" disabled={rerunning} onclick={rerun}>
				{#if rerunning}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{/if}Run patterns
			</Button>
		{/snippet}
	</EmptyState>
{:else}
	<div class="space-y-6">
		<div class="flex flex-wrap items-center gap-2">
			<Select.Simple
				size="sm"
				aria-label="Pattern run"
				bind:value={selectedId}
				items={runs.map((r) => ({ value: r.id, label: runLabel(r), description: `detectors ${shortSha(r.detector_set_hash, 10)} · ${r.detectors.length} detectors` }))}
			/>
			{#if detail}
				{#if detail.is_live}
					<Badge variant="secondary" title="Primary features scored as variants finish; replaced by the post-run PatternRun">provisional</Badge>
				{:else}
					<Badge variant={detail.label === 'planned' ? 'default' : 'outline'} title={detail.label === 'planned' ? 'Matches the pre-registered pattern plan' : 'Not the pre-registered plan: exploratory'}>{detail.label}</Badge>
					<StatusBadge status={detail.status} />
				{/if}
				{#if summary.interim}<Badge variant="secondary" title="The experiment is still running: p and q are withheld">interim</Badge>{/if}
			{/if}
			<div class="ml-auto flex gap-2">
				<Button variant="outline" size="sm" disabled={rerunning} onclick={rerun} title="New PatternRun over the existing variants (normalise, cluster, detect); no model calls">
					{#if rerunning}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<RotateCw class="mr-1.5 h-3.5 w-3.5" />{/if}
					Re-run patterns (no LLM cost)
				</Button>
				{#if detail && !detail.is_live && detail.status === 'completed'}
					<Button variant="outline" size="sm" href={patternExportUrl(detail.id)} download><Download class="mr-1.5 h-3.5 w-3.5" />Export</Button>
				{/if}
			</div>
		</div>

		{#if error}<ErrorAlert message={error} />{/if}

		{#if detail && !ready}
			<Card.Root>
				<Card.Content class="py-4 text-sm">
					{#if detail.status === 'failed'}
						<ErrorAlert title="The pattern run failed" message={summary.error ?? 'See the post-run log.'} />
					{:else}
						<LoaderCircle class="mr-1.5 inline h-4 w-4 animate-spin motion-reduce:animate-none" /> The pattern run is {detail.status}; results appear when it completes.
					{/if}
				</Card.Content>
			</Card.Root>
		{:else if detail}
			<p class="text-xs text-muted-foreground">
				{summary.n_planned ?? '—'} planned variants · {summary.n_with_output ?? '—'} with output · {summary.n_unparsed ?? 0} not normalisable ·
				{summary.detectors?.length ?? detail.detectors.length} detectors · normaliser v{detail.normalizer_version} ·
				detector set <span class="font-mono">{shortSha(detail.detector_set_hash, 12)}</span>
				{#if summary.planned_features?.length}· primary features <span class="font-mono">{summary.planned_features.join(', ')}</span>{/if}
			</p>
			{#if summary.issues?.length}
				<ul class="space-y-1 text-xs">
					{#each summary.issues as issue, i (i)}
						<li><StatusBadge status={issue.severity} tone={issue.severity === 'error' ? 'danger' : issue.severity === 'warn' ? 'warning' : 'neutral'} label={issue.code} class="normal-case" /> {issue.message}</li>
					{/each}
				</ul>
			{/if}

			<section class="space-y-3">
				<SectionHeading size="sm" title="Prevalence" lead="Presence of each feature per condition and target; never a severity." />
				<PrevalenceTable patternRunId={detail.id} {conditionLabels} {targetLabels} onaudit={detail.is_live ? undefined : audit} />
			</section>

			{#if !detail.is_live}
				<section class="space-y-3">
					<SectionHeading size="sm" title="Comparisons" lead="Condition pairs per feature; the method used is stated per row." />
					<ComparisonTable patternRunId={detail.id} />
				</section>

				<section class="space-y-3">
					<SectionHeading size="sm" title="Clusters" lead="How many distinct implementations the variants converge on, per target." />
					<ClusterPanel patternRunId={detail.id} {summary} />
				</section>

				<section class="space-y-3">
					<SectionHeading size="sm" title="Feature matrix" aside={matrixAside} />
					{#if matrixTarget}
						<FeatureMatrix {studyId} targetId={matrixTarget} features={summary.features ?? []} {conditionLabels} />
					{/if}
				</section>

				<section class="space-y-3">
					<SectionHeading size="sm" title="Detector audits" count={audits.length} lead="A stratified sample of hits and non-hits coded by people in Human eval gives each detector's precision and NPV." />
					{#if audits.length === 0}
						<p class="text-sm text-muted-foreground">No audit yet. Use “Audit” on a feature in the prevalence table.</p>
					{:else}
						<ul class="space-y-1 text-sm">
							{#each audits as a (a.id)}
								<li class="flex flex-wrap items-center gap-2">
									<span class="font-mono">{a.feature_key}</span>
									<StatusBadge status={a.status} />
									<span class="text-xs text-muted-foreground">{a.sample.hits.length} hits + {a.sample.non_hits.length} non-hits · seed {a.sample.seed}</span>
									{#if a.status === 'complete'}
										<span class="text-xs">precision {a.precision === null ? '—' : fmtProportion(a.precision)} · NPV {a.npv === null ? '—' : fmtProportion(a.npv)}</span>
									{/if}
									{#if a.eval_task}<a class="text-xs text-primary hover:underline" href={`/human-eval/${a.eval_task}`}>open the coding task</a>{/if}
								</li>
							{/each}
						</ul>
					{/if}
				</section>
			{/if}
		{/if}
	</div>
{/if}

{#snippet matrixAside()}
	{#if targetIds.length > 1}
		<Select.Simple size="sm" aria-label="Target" bind:value={matrixTarget} items={targetIds.map((id) => ({ value: id, label: targetLabels[id] ?? id.slice(0, 8) }))} />
	{/if}
{/snippet}
