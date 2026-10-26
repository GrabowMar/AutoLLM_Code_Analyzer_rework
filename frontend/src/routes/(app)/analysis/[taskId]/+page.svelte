<script lang="ts">
	// One analysis run: header (status, completeness, target, profile, plan
	// hash, actions), coverage banner and URL-synced tabs (Overview, Findings,
	// Issues, AI review, Files, Provenance). Polls silently every 3 s while
	// the run is queued or running; tabs reload their lists when it progresses.
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import type { AnalysisRunDetail, IssueKind } from '$lib/api/analysis';
	import {
		cancelAnalysisRun,
		deleteAnalysisRun,
		getAnalysisRun,
		isMeasurement,
		isRunActive,
		retryAnalysisRun
	} from '$lib/api/analysis';
	import { formatApiError } from '$lib/api/core';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { isNotFoundError } from '$lib/components/generator/batches/batch-utils';
	import CoverageBanner from '$lib/components/analysis/CoverageBanner.svelte';
	import CopyValue from '$lib/components/analysis/CopyValue.svelte';
	import Hint from '$lib/components/analysis/Hint.svelte';
	import {
		failureReasonLabel,
		fmtAgo,
		fmtDateTime,
		fmtSeconds,
		PURPOSE_LABELS,
		retriableTools,
		shortHash,
		shortId,
		toolProblems
	} from '$lib/components/analysis/run-utils';
	import RunOverview from '$lib/components/analysis/run/RunOverview.svelte';
	import RunFindings from '$lib/components/analysis/run/RunFindings.svelte';
	import RunIssues from '$lib/components/analysis/run/RunIssues.svelte';
	import RunAiReview from '$lib/components/analysis/run/RunAiReview.svelte';
	import RunFiles from '$lib/components/analysis/run/RunFiles.svelte';
	import RunProvenance from '$lib/components/analysis/run/RunProvenance.svelte';
	import RunExportMenu from '$lib/components/analysis/run/RunExportMenu.svelte';
	import {
		FINDING_PARAMS,
		replaceParams,
		RUN_TABS,
		withParams,
		type ParamUpdates,
		type RunTab
	} from '$lib/components/analysis/run/url';
	import Ban from '@lucide/svelte/icons/ban';
	import FileCode from '@lucide/svelte/icons/file-code';
	import Fingerprint from '@lucide/svelte/icons/fingerprint';
	import Layers from '@lucide/svelte/icons/layers';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import ListTree from '@lucide/svelte/icons/list-tree';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import SearchX from '@lucide/svelte/icons/search-x';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Trash2 from '@lucide/svelte/icons/trash-2';

	const POLL_MS = 3_000;

	const runId = $derived(page.params.taskId ?? '');

	let run = $state<AnalysisRunDetail | null>(null);
	let loading = $state(true);
	let refreshing = $state(false);
	let notFound = $state(false);
	let error = $state('');
	let refreshError = $state('');
	let seq = 0;

	async function load(id: string, silent = false) {
		const mine = ++seq;
		if (!silent) refreshing = true;
		try {
			const detail = await getAnalysisRun(id);
			if (mine !== seq) return;
			run = detail;
			error = '';
			refreshError = '';
			notFound = false;
		} catch (e) {
			if (mine !== seq) return;
			if (isNotFoundError(e)) {
				notFound = true;
				run = null;
			} else if (run) {
				// Keep the last good render; the next poll retries.
				refreshError = formatApiError(e, 'Could not refresh this run.');
			} else {
				error = formatApiError(e, 'Could not load this run.');
			}
		} finally {
			if (mine === seq) {
				loading = false;
				refreshing = false;
			}
		}
	}

	// A new id starts from a clean slate.
	$effect(() => {
		const id = runId;
		untrack(() => {
			run = null;
			loading = true;
			notFound = false;
			error = '';
			refreshError = '';
			void load(id);
		});
	});

	const active = $derived(!!run && isRunActive(run.status));

	$effect(() => {
		if (!active) return;
		const id = runId;
		const timer = setInterval(() => {
			if (!document.hidden) void load(id, true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	/** Changes whenever the run progresses: tabs reload their lists silently. */
	const revision = $derived(
		run ? `${run.status}|${run.updated_at}|${run.results.map((r) => r.status).join(',')}` : ''
	);

	// ── Tabs (URL-synced) ──────────────────────────────────────────────
	function parseTab(raw: string | null): RunTab {
		return (RUN_TABS as readonly string[]).includes(raw ?? '') ? (raw as RunTab) : 'overview';
	}
	const tab = $derived(parseTab(page.url.searchParams.get('tab')));

	function setTab(next: string) {
		void replaceParams(page.url, { tab: next === 'overview' ? null : next });
	}

	/** Jump to another tab with fresh filters (a history entry, so Back returns). */
	function openTab(next: RunTab, updates: ParamUpdates = {}, clear: readonly string[] = []) {
		void goto(withParams(page.url, { tab: next === 'overview' ? null : next, ...updates }, clear), {
			noScroll: true
		});
	}

	function showFindingsOfFile(path: string) {
		openTab('findings', { file: path }, FINDING_PARAMS);
	}
	function showFindingsOfIssue(kind: IssueKind, issueKey: string) {
		openTab('findings', { kind: kind === 'ai' ? 'ai' : null, issue: issueKey }, FINDING_PARAMS);
	}
	function showFindingsOfSample(index: number) {
		openTab('findings', { kind: 'ai', sample: index }, FINDING_PARAMS);
	}
	function openFile(path: string) {
		openTab('files', { path });
	}
	function showTools() {
		if (tab !== 'overview') setTab('overview');
		requestAnimationFrame(() =>
			document.getElementById('run-tools')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
		);
	}

	// ── Actions ────────────────────────────────────────────────────────
	let cancelOpen = $state(false);
	let cancelling = $state(false);
	let deleteOpen = $state(false);
	let deleting = $state(false);
	let retryOpen = $state(false);
	let retrying = $state<string | null>(null);
	let retrySelection = $state<Set<string>>(new Set());

	const retriable = $derived(run && !active ? retriableTools(run.results) : []);
	const canDelete = $derived(!!run && !active && !run.study_id);

	async function confirmCancel() {
		if (!run) return;
		cancelling = true;
		try {
			run = await cancelAnalysisRun(run.id);
			toast.success('Cancellation requested');
			cancelOpen = false;
		} catch (e) {
			toast.error(formatApiError(e, 'Could not cancel the run.'));
			void load(runId, true);
		} finally {
			cancelling = false;
		}
	}

	async function confirmDelete() {
		if (!run) return;
		deleting = true;
		try {
			await deleteAnalysisRun(run.id);
			toast.success('Run deleted');
			deleteOpen = false;
			await goto('/analysis');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not delete the run.'));
		} finally {
			deleting = false;
		}
	}

	function openRetry() {
		retrySelection = new Set(retriable.map((r) => r.tool_slug));
		retryOpen = true;
	}

	async function retry(tools: string[]) {
		if (!run || tools.length === 0) return;
		retrying = tools.length === 1 ? tools[0] : '*';
		const all = tools.length === retriable.length;
		try {
			run = await retryAnalysisRun(run.id, all ? undefined : tools);
			toast.success(tools.length === 1 ? `Requeued ${tools[0]}` : `Requeued ${tools.length} tools`);
			retryOpen = false;
		} catch (e) {
			toast.error(formatApiError(e, 'Could not retry.'));
		} finally {
			retrying = null;
		}
	}

	function toggleRetry(slug: string, on: boolean) {
		const next = new Set(retrySelection);
		if (on) next.add(slug);
		else next.delete(slug);
		retrySelection = next;
	}

	// ── Header details ─────────────────────────────────────────────────
	const m = $derived(run && isMeasurement(run.measurement) ? run.measurement : null);
	const staticCount = $derived(
		run
			? run.results
					.filter((r) => r.tool_kind !== 'ai')
					.reduce((n, r) => n + (r.findings?.total ?? 0), 0)
			: 0
	);
	// Stored findings are a lower bound while the run is active or when a
	// static tool produced no data (failed / timed out / skipped / cancelled).
	const staticPartial = $derived(
		!!run && (active || toolProblems(run.results).some((p) => !p.isAi))
	);
	const aiResult = $derived(run?.results.find((r) => r.tool_kind === 'ai') ?? null);
</script>

{#if loading && !run}
	<div class="space-y-6">
		<PageHeader title="Analysis run" back={{ href: '/analysis', label: 'Runs' }} />
		<LoadingState label="loading run" />
	</div>
{:else if notFound}
	<div class="space-y-6">
		<PageHeader title="Run not found" back={{ href: '/analysis', label: 'Runs' }} />
		<EmptyState
			icon={SearchX}
			title="Run not found"
			description="It may have been deleted, or the link is wrong."
		>
			{#snippet action()}
				<Button variant="outline" size="sm" href="/analysis">All runs</Button>
			{/snippet}
		</EmptyState>
	</div>
{:else if !run}
	<div class="space-y-6">
		<PageHeader title="Analysis run" back={{ href: '/analysis', label: 'Runs' }} />
		<ErrorAlert message={error || 'Could not load this run.'} onretry={() => load(runId)} />
	</div>
{:else}
	{@const r = run}
	<div class="space-y-6">
		<PageHeader
			title={r.name || `Run ${shortId(r.id)}`}
			documentTitle="Run {shortId(r.id)}"
			back={{ href: '/analysis', label: 'Runs' }}
		>
			{#snippet meta()}
				<StatusBadge status={r.status} />
				{#if !active}
					{#if r.is_complete}
						<StatusBadge status="complete" tone="success" />
					{:else}
						<Hint
							text="A required tool did not succeed: totals are lower bounds and dependent outcomes are not available"
							underline={false}
						>
							<StatusBadge status="incomplete" tone="warning" />
						</Hint>
					{/if}
				{/if}
				{#if r.stale}
					<StatusBadge status="stale" />
				{/if}
				<Badge variant="outline">{PURPOSE_LABELS[r.purpose] ?? r.purpose}</Badge>
				{#if r.source === 'inline'}
					<Hint
						text="Pasted source has no model, app or generation metadata: use it for quick checks, not research"
						underline={false}
					>
						<Badge variant="outline" class="border-warning/40 text-warning-fg">non-research</Badge>
					</Hint>
				{/if}
			{/snippet}
			{#snippet actions()}
				<Button variant="outline" size="sm" onclick={() => load(runId)} disabled={refreshing}>
					<RefreshCw
						class="size-3.5 {refreshing ? 'animate-spin motion-reduce:animate-none' : ''}"
					/>Refresh
				</Button>
				<RunExportMenu runId={r.id} partial={active} />
				{#if retriable.length > 0}
					<Button variant="outline" size="sm" onclick={openRetry} disabled={!!retrying}>
						<RotateCcw class="size-3.5" />Retry
					</Button>
				{/if}
				{#if canDelete}
					<Button variant="outline" size="sm" onclick={() => (deleteOpen = true)}>
						<Trash2 class="size-3.5" />Delete
					</Button>
				{/if}
				{#if active && !r.cancel_requested}
					<Button variant="destructive" size="sm" onclick={() => (cancelOpen = true)}>
						<Ban class="size-3.5" />Cancel run
					</Button>
				{/if}
			{/snippet}
			<div class="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
				<span class="min-w-0 break-words">
					Target:
					{#if r.generation_job_id}
						<a
							href="/applications/{r.generation_job_id}"
							class="text-primary underline-offset-2 hover:underline">{r.target.label}</a
						>
					{:else}
						<span class="text-foreground">{r.target.label}</span>
					{/if}
				</span>
				<span>
					Profile:
					{#if r.profile}
						<a
							href="/analysis/profiles/{r.profile.id}"
							class="text-primary underline-offset-2 hover:underline"
							>{r.profile.name} · v{r.profile.version}</a
						>
					{:else}
						<span class="text-foreground">inline config</span>
					{/if}
				</span>
				{#if r.plan_hash}
					<span class="inline-flex items-center gap-1">
						Plan <CopyValue
							value={r.plan_hash}
							display={shortHash(r.plan_hash, 12)}
							label="plan hash"
						/>
						<a
							href="/analysis?plan_hash={encodeURIComponent(r.plan_hash)}"
							class="text-primary underline-offset-2 hover:underline"
							title="Runs under the same frozen plan: the only runs comparable with this one"
							>comparable runs</a
						>
					</span>
				{/if}
				{#if r.study_id}
					<a
						href="/analysis/studies/{r.study_id}"
						class="text-primary underline-offset-2 hover:underline">Study</a
					>
				{/if}
				<span title={fmtDateTime(r.created_at)}>
					Created {fmtAgo(r.created_at)}{#if r.duration_seconds != null}{' · '}took
						{fmtSeconds(r.duration_seconds)}{/if}
				</span>
			</div>
		</PageHeader>

		{#if refreshError}
			<ErrorAlert message={refreshError} onretry={() => load(runId)} />
		{/if}

		<CoverageBanner run={r} ontools={showTools} />

		<Tabs.Root value={tab} onValueChange={setTab}>
			<Tabs.List aria-label="Run sections">
				<Tabs.Trigger value="overview"><LayoutDashboard />Overview</Tabs.Trigger>
				<Tabs.Trigger value="findings">
					<ListTree />Findings
					<span
						class="font-mono text-xs text-muted-foreground"
						title={staticPartial
							? 'Lower bound: the run is still active or a static tool produced no data'
							: undefined}>({staticPartial ? '≥ ' : ''}{staticCount})</span
					>
				</Tabs.Trigger>
				<Tabs.Trigger value="issues">
					<Layers />Issues
					{#if m}<span class="font-mono text-xs text-muted-foreground"
							>({m.static.issues.total})</span
						>{/if}
				</Tabs.Trigger>
				<Tabs.Trigger value="ai">
					<Sparkles />AI review
					{#if aiResult && m?.ai?.available}
						<span class="font-mono text-xs text-muted-foreground">({m.ai.consensus.total})</span>
					{:else if aiResult && aiResult.failure_reason}
						<span class="text-xs text-danger-fg">{failureReasonLabel(aiResult.failure_reason)}</span
						>
					{:else if !aiResult}
						<span class="text-xs text-muted-foreground">off</span>
					{/if}
				</Tabs.Trigger>
				<Tabs.Trigger value="files">
					<FileCode />Files
					<span class="font-mono text-xs text-muted-foreground">({r.manifest?.files ?? 0})</span>
				</Tabs.Trigger>
				<Tabs.Trigger value="provenance"><Fingerprint />Provenance</Tabs.Trigger>
			</Tabs.List>

			<!-- bits-ui keeps inactive panels mounted: render only the active
           tab so hidden tabs do not fetch or poll. -->
			<Tabs.Content value="overview">
				{#if tab === 'overview'}
					<RunOverview
						run={r}
						onretry={(slug) => retry([slug])}
						{retrying}
						ontab={(t) => openTab(t)}
					/>
				{/if}
			</Tabs.Content>
			<Tabs.Content value="findings">
				{#if tab === 'findings'}
					<RunFindings run={r} {revision} onfile={openFile} />
				{/if}
			</Tabs.Content>
			<Tabs.Content value="issues">
				{#if tab === 'issues'}
					<RunIssues run={r} {revision} onfindings={showFindingsOfIssue} />
				{/if}
			</Tabs.Content>
			<Tabs.Content value="ai">
				{#if tab === 'ai'}
					<RunAiReview
						run={r}
						{revision}
						onfindings={(kind, key) => showFindingsOfIssue(kind, key)}
						onsample={showFindingsOfSample}
					/>
				{/if}
			</Tabs.Content>
			<Tabs.Content value="files">
				{#if tab === 'files'}
					<RunFiles run={r} {revision} onfindings={showFindingsOfFile} />
				{/if}
			</Tabs.Content>
			<Tabs.Content value="provenance">
				{#if tab === 'provenance'}
					<RunProvenance run={r} />
				{/if}
			</Tabs.Content>
		</Tabs.Root>
	</div>

	<ConfirmDialog
		bind:open={cancelOpen}
		title="Cancel this run?"
		description="The running tool is stopped and the remaining tools are cancelled. Tools that already finished keep their results; the run stays incomplete."
		confirmLabel="Cancel run"
		cancelLabel="Keep running"
		destructive
		busy={cancelling}
		onConfirm={confirmCancel}
	/>

	<ConfirmDialog
		bind:open={deleteOpen}
		title="Delete this run?"
		description="The run, its findings and its stored raw outputs are deleted. This cannot be undone."
		confirmLabel="Delete run"
		destructive
		busy={deleting}
		onConfirm={confirmDelete}
	/>

	<Dialog.Root bind:open={retryOpen}>
		<Dialog.Content class="max-w-md">
			<Dialog.Header>
				<Dialog.Title>Retry tools</Dialog.Title>
				<Dialog.Description>
					Requeue tools that produced no data. The run is measured again when they finish; tools
					that succeeded keep their results.{#if r.study_id}{' '}Retrying a run of a completed study
						re-opens the study.{/if}
				</Dialog.Description>
			</Dialog.Header>
			<ul class="my-2 space-y-1.5">
				{#each retriable as t (t.id)}
					<li>
						<label class="flex cursor-pointer items-center gap-2 text-sm">
							<Checkbox
								checked={retrySelection.has(t.tool_slug)}
								onCheckedChange={(v) => toggleRetry(t.tool_slug, v === true)}
							/>
							<span class="font-mono text-xs">{t.tool_slug}</span>
							<span class="text-xs text-muted-foreground">
								{t.status.replace(/_/g, ' ')}{t.failure_reason
									? ` · ${failureReasonLabel(t.failure_reason)}`
									: ''}
							</span>
						</label>
					</li>
				{/each}
			</ul>
			<Dialog.Footer>
				<Button
					variant="outline"
					size="sm"
					disabled={!!retrying}
					onclick={() => (retryOpen = false)}>Cancel</Button
				>
				<Button
					size="sm"
					disabled={!!retrying || retrySelection.size === 0}
					onclick={() => retry([...retrySelection])}
				>
					{#if retrying}<LoaderCircle
							class="size-3.5 animate-spin motion-reduce:animate-none"
						/>{/if}
					Retry {retrySelection.size} tool{retrySelection.size === 1 ? '' : 's'}
				</Button>
			</Dialog.Footer>
		</Dialog.Content>
	</Dialog.Root>
{/if}
