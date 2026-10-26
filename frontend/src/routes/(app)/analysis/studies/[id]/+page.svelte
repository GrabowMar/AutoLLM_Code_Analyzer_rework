<script lang="ts">
	/**
	 * Study detail — lifecycle actions by status and URL-synced tabs:
	 * Overview (live progress / next step), Protocol (frozen, hash-verified),
	 * Results (typed post-run report; 202 generation polled), Runs, Settings
	 * (draft: edit through the setup steps; locked: the frozen plan).
	 * While a study runs, a launch is submitting or a report is generating,
	 * the page refreshes silently (no skeleton flash).
	 */
	import { onDestroy } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import {
		cancelStudy,
		deleteStudy,
		getStudy,
		getStudyProgress,
		getStudyProtocol,
		getStudyReport,
		getTargetOptions,
		hasTargetSpec,
		isFrozenPlan,
		launchStudy,
		requestStudyReport,
		retryFailedStudyRuns,
		runStudyPreflight,
		waitForStudyReport,
		isPreflightReport,
		type StudyDetail,
		type StudyProgress,
		type StudyProtocolEnvelope,
		type StudyReportEnvelope,
		type TargetOptions,
		studyFileStem,
		downloadStudyProtocolMarkdown,
		downloadStudyReportMarkdown
	} from '$lib/api/analysis';
	import { saveJson } from '$lib/utils/download';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { loadProfileDefaults } from '$lib/components/analysis/profile/index';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import LayoutDashboard from '@lucide/svelte/icons/layout-dashboard';
	import ScrollText from '@lucide/svelte/icons/scroll-text';
	import ChartColumn from '@lucide/svelte/icons/chart-column';
	import Microscope from '@lucide/svelte/icons/microscope';
	import Settings2 from '@lucide/svelte/icons/settings-2';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import CircleStop from '@lucide/svelte/icons/circle-stop';
	import FileChartColumn from '@lucide/svelte/icons/file-chart-column';
	import FileDown from '@lucide/svelte/icons/file-down';
	import FileJson from '@lucide/svelte/icons/file-json';
	import Play from '@lucide/svelte/icons/play';
	import Rocket from '@lucide/svelte/icons/rocket';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Lock from '@lucide/svelte/icons/lock';
	import StudyProgressPanel from '$lib/components/analysis/study/StudyProgressPanel.svelte';
	import StudyRunsTable from '$lib/components/analysis/study/StudyRunsTable.svelte';
	import ProtocolView from '$lib/components/analysis/study/ProtocolView.svelte';
	import ReportView from '$lib/components/analysis/study/ReportView.svelte';
	import PreflightView from '$lib/components/analysis/study/PreflightView.svelte';
	import LaunchPanel from '$lib/components/analysis/study/LaunchPanel.svelte';
	import PlanView from '$lib/components/analysis/study/PlanView.svelte';
	import ExportMenu from '$lib/components/analysis/study/ExportMenu.svelte';
	import Hint from '$lib/components/analysis/Hint.svelte';
	import {
		CORRECTION_LABELS,
		DESIGN_FACTOR_LABELS,
		STUDY_STATUS_DESCRIPTIONS,
		factorList,
		fmtDateTime,
		fmtUsd,
		profileLabel,
		runBuckets,
		shortHash,
		targetSpecSummary
	} from '$lib/components/analysis/study/format';

	type Tab = 'overview' | 'protocol' | 'results' | 'runs' | 'settings';
	const TABS: Tab[] = ['overview', 'protocol', 'results', 'runs', 'settings'];
	const POLL_MS = 4000;

	const studyId = $derived(page.params.id ?? '');
	const tab = $derived.by((): Tab => {
		const raw = page.url.searchParams.get('tab') as Tab | null;
		return raw && TABS.includes(raw) ? raw : 'overview';
	});

	function setTab(next: string) {
		const url = new URL(page.url);
		if (next === 'overview') url.searchParams.delete('tab');
		else url.searchParams.set('tab', next);
		void goto(url, { replaceState: true, keepFocus: true, noScroll: true });
	}

	// ── Study + progress ──────────────────────────────────────────────────
	let study = $state<StudyDetail | null>(null);
	let progress = $state<StudyProgress | null>(null);
	let options = $state<TargetOptions | null>(null);
	let outcomeLabels = $state<Record<string, string>>({});
	let loading = $state(true);
	let error = $state('');
	let refreshKey = $state(0);
	let loadedFor = '';

	const launched = $derived(
		!!study &&
			(study.status === 'running' || study.status === 'completed' || study.status === 'cancelled')
	);

	async function load(silent = false) {
		if (!studyId) return;
		if (!silent) loading = true;
		try {
			const s = await getStudy(studyId);
			study = s;
			if (s.status !== 'draft' && s.status !== 'locked') {
				progress = await getStudyProgress(studyId);
			} else {
				progress = null;
			}
			error = '';
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the study.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const id = studyId;
		if (!id || loadedFor === id) return;
		loadedFor = id;
		study = null;
		progress = null;
		reportEnv = null;
		reportMissing = false;
		protocol = null;
		void load();
		getTargetOptions()
			.then((o) => (options = o))
			.catch(() => {});
		loadProfileDefaults()
			.then(
				(d) =>
					(outcomeLabels = Object.fromEntries(
						Object.entries(d.outcomes).map(([k, m]) => [k, m.label])
					))
			)
			.catch(() => {});
	});

	const live = $derived(
		!!study &&
			(study.status === 'running' ||
				!!study.launch?.pending ||
				study.report_state?.status === 'generating')
	);

	$effect(() => {
		if (!live) return;
		const timer = setInterval(() => {
			if (document.visibilityState !== 'visible') return;
			void load(true);
			refreshKey += 1;
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	// ── Lifecycle actions ─────────────────────────────────────────────────
	let busy = $state<'' | 'cancel' | 'retry' | 'delete' | 'resume' | 'preflight' | 'report'>('');
	let cancelOpen = $state(false);
	let deleteOpen = $state(false);
	let retryOpen = $state(false);

	async function act<T>(
		key: typeof busy,
		fn: () => Promise<T>,
		done: (result: T) => void,
		fallback: string
	) {
		busy = key;
		try {
			done(await fn());
		} catch (e) {
			toast.error(formatApiError(e, fallback));
		} finally {
			busy = '';
		}
	}

	function doCancel() {
		void act(
			'cancel',
			() => cancelStudy(studyId),
			(s) => {
				study = s;
				cancelOpen = false;
				toast.success('Study cancelled; active runs are stopping');
				void load(true);
			},
			'Could not cancel the study.'
		);
	}

	function doRetry() {
		void act(
			'retry',
			() => retryFailedStudyRuns(studyId),
			(res) => {
				study = res.study;
				retryOpen = false;
				toast.success(
					`${res.retried.length} run${res.retried.length === 1 ? '' : 's'} requeued` +
						(res.skipped.length ? ` · ${res.skipped.length} skipped` : '')
				);
				refreshKey += 1;
				void load(true);
			},
			'Could not retry the failed runs.'
		);
	}

	function doDelete() {
		void act(
			'delete',
			() => deleteStudy(studyId),
			(res) => {
				deleteOpen = false;
				toast.success(`Study deleted with ${res.runs} run${res.runs === 1 ? '' : 's'}`);
				void goto('/analysis/studies');
			},
			'Could not delete the study.'
		);
	}

	function doResume() {
		void act(
			'resume',
			() => launchStudy(studyId),
			(s) => {
				study = s;
				toast.success('Launch resumed');
				void load(true);
			},
			'Could not resume the launch.'
		);
	}

	function doPreflight() {
		void act(
			'preflight',
			() => runStudyPreflight(studyId),
			() => void load(true),
			'The preflight failed.'
		);
	}

	const setupStep = $derived.by(() => {
		if (!study) return 'question';
		if (!hasTargetSpec(study.target_spec)) return 'targets';
		if (!study.profile) return 'method';
		if (!study.preflight_summary || study.preflight_summary.blocking > 0) return 'preflight';
		return 'launch';
	});

	// ── Protocol (lazy) ───────────────────────────────────────────────────
	let protocol = $state<StudyProtocolEnvelope | null>(null);
	let protocolError = $state('');
	let protocolLoading = $state(false);

	$effect(() => {
		if (tab !== 'protocol' || !study?.protocol_hash || protocol || protocolLoading || protocolError)
			return;
		protocolLoading = true;
		getStudyProtocol(studyId)
			.then((p) => (protocol = p))
			.catch((e) => (protocolError = formatApiError(e, 'Could not load the protocol.')))
			.finally(() => (protocolLoading = false));
	});

	// A new lock (unlock → lock) changes the protocol: drop the cached one.
	$effect(() => {
		const hash = study?.protocol_hash ?? '';
		if (protocol && protocol.protocol_hash !== hash) {
			protocol = null;
			protocolError = '';
		}
	});

	// ── Report (lazy; 202 generation polled) ──────────────────────────────
	let reportEnv = $state<StudyReportEnvelope | null>(null);
	let reportMissing = $state(false);
	let reportLoading = $state(false);
	let reportError = $state('');
	let reportWaiting = $state(false);
	let reportCtrl: AbortController | null = null;

	onDestroy(() => reportCtrl?.abort());

	async function fetchReport() {
		reportLoading = true;
		reportError = '';
		try {
			const env = await getStudyReport(studyId);
			reportEnv = env;
			reportMissing = false;
			if (env.status === 'generating') void waitReport();
		} catch (e) {
			const body = e as { detail?: unknown } | null;
			if (
				body &&
				typeof body.detail === 'string' &&
				/no report has been requested/i.test(body.detail)
			) {
				reportMissing = true;
				reportEnv = null;
			} else {
				reportError = formatApiError(e, 'Could not load the report.');
			}
		} finally {
			reportLoading = false;
		}
	}

	async function waitReport() {
		if (reportWaiting) return;
		reportCtrl?.abort();
		const ctrl = new AbortController();
		reportCtrl = ctrl;
		reportWaiting = true;
		try {
			const env = await waitForStudyReport(studyId, {
				signal: ctrl.signal,
				onUpdate: (e) => {
					if (e.status === 'generating') reportEnv = e;
				}
			});
			reportEnv = env;
			if (env.status === 'ready') toast.success('Report ready');
			void load(true);
		} catch (e) {
			if (!isAbortError(e))
				reportError = formatApiError(e, 'The report is taking long; check again later.');
		} finally {
			if (reportCtrl === ctrl) reportWaiting = false;
		}
	}

	$effect(() => {
		if (tab !== 'results' || !study || reportEnv || reportMissing || reportLoading || reportError)
			return;
		if (study.status === 'draft' || study.status === 'locked') return;
		void fetchReport();
	});

	function generateReport() {
		reportError = '';
		void act(
			'report',
			() => requestStudyReport(studyId),
			(env) => {
				reportEnv = env;
				reportMissing = false;
				if (tab !== 'results') setTab('results');
				void waitReport();
			},
			'Could not start the report.'
		);
	}

	const reportReady = $derived(
		reportEnv?.status === 'ready' || study?.report_state?.status === 'ready'
	);

	async function download(fn: () => Promise<string>) {
		try {
			await fn();
		} catch (e) {
			toast.error(formatApiError(e, 'The download failed.'));
		}
	}

	const buckets = $derived(runBuckets(study?.run_counts));
	const retryable = $derived(buckets.failed + buckets.partial + buckets.cancelled);
	const preflightReport = $derived(
		study && isPreflightReport(study.preflight) ? study.preflight : null
	);
</script>

<div class="space-y-6">
	<PageHeader
		title={study?.name ?? 'Study'}
		description={study ? study.description || STUDY_STATUS_DESCRIPTIONS[study.status] : undefined}
		back={{ href: '/analysis/studies', label: 'Studies' }}
	>
		{#snippet meta()}
			{#if study}
				<StatusBadge status={study.status} />
				{#if study.launch?.pending}<StatusBadge status={study.launch.status} />{/if}
				{#if study.protocol_hash}
					<Hint
						underline={false}
						text="Protocol sha256 {study.protocol_hash}{study.protocol_verified === false
							? ' — integrity check FAILED'
							: ''}"
					>
						<span class="mono-tag {study.protocol_verified === false ? 'text-danger-fg' : ''}">
							<Lock class="mr-1 h-3 w-3" aria-hidden="true" />{shortHash(study.protocol_hash, 8)}
						</span>
					</Hint>
				{/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if study}
				{#if study.status === 'draft'}
					{#if study.actions.delete}
						<Button variant="outline" size="sm" onclick={() => (deleteOpen = true)}
							><Trash2 />Delete</Button
						>
					{/if}
					<Button size="sm" href="/analysis/studies/new?id={study.id}&step={setupStep}">
						Continue setup<ArrowRight />
					</Button>
				{:else if study.status === 'locked'}
					{#if tab !== 'overview'}
						<Button size="sm" onclick={() => setTab('overview')}
							><Rocket />Review &amp; launch</Button
						>
					{/if}
				{:else}
					<ExportMenu {study} {reportReady} />
					{#if study.actions.retry_failed && retryable > 0}
						<Button
							variant="outline"
							size="sm"
							onclick={() => (retryOpen = true)}
							disabled={busy !== ''}
						>
							<RotateCcw />Retry failed ({retryable})
						</Button>
					{/if}
					{#if study.status === 'running' && study.actions.cancel}
						<Button
							variant="outline"
							size="sm"
							onclick={() => (cancelOpen = true)}
							disabled={busy !== ''}
						>
							<CircleStop />Cancel
						</Button>
					{/if}
					{#if study.launch?.pending && study.launch.status === 'interrupted'}
						<Button size="sm" onclick={doResume} disabled={busy !== ''}>
							{#if busy === 'resume'}<LoaderCircle
									class="animate-spin motion-reduce:animate-none"
								/>{:else}<Play />{/if}Resume launch
						</Button>
					{:else if study.actions.report}
						<Button
							size="sm"
							onclick={generateReport}
							disabled={busy !== '' || reportWaiting || study.report_state?.status === 'generating'}
						>
							{#if busy === 'report' || reportWaiting || study.report_state?.status === 'generating'}
								<LoaderCircle class="animate-spin motion-reduce:animate-none" />
							{:else}
								<FileChartColumn />
							{/if}
							{study.report_state?.status === 'generating' || reportWaiting
								? 'Generating report'
								: study.report_state?.status === 'ready'
									? 'Regenerate report'
									: 'Generate report'}
						</Button>
					{/if}
				{/if}
			{/if}
		{/snippet}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => load()} />{/if}

	{#if loading && !study}
		<LoadingState label="loading study" />
	{:else if study}
		<Tabs.Root value={tab} onValueChange={setTab}>
			<Tabs.List aria-label="Study sections">
				<Tabs.Trigger value="overview"><LayoutDashboard />Overview</Tabs.Trigger>
				<Tabs.Trigger value="protocol" disabled={!study.protocol_hash}
					><ScrollText />Protocol</Tabs.Trigger
				>
				<Tabs.Trigger value="results">
					<ChartColumn />Results
					{#if study.report_state?.status === 'ready'}<span
							class="h-1.5 w-1.5 rounded-full bg-success"
							aria-label="report ready"
						></span>{/if}
				</Tabs.Trigger>
				<Tabs.Trigger value="runs">
					<Microscope />Runs
					{#if buckets.total > 0}<span class="text-[11px] tabular-nums text-muted-foreground"
							>{buckets.total}</span
						>{/if}
				</Tabs.Trigger>
				<Tabs.Trigger value="settings"><Settings2 />Settings</Tabs.Trigger>
			</Tabs.List>

			<!-- bits-ui keeps inactive panels mounted: render only the active tab so
			     hidden tabs do not fetch, poll or draw charts. -->
			<!-- Overview -->
			<Tabs.Content value="overview" class="space-y-4">
				{#if tab === 'overview'}
					{#if study.status === 'draft'}
						<Card.Root>
							<Card.Header>
								<Card.Title>Next step</Card.Title>
								<Card.Description>
									{#if setupStep === 'targets'}Choose the targets to analyse.
									{:else if setupStep === 'method'}Pick the analysis profile and design.
									{:else if setupStep === 'preflight'}Run the preflight and resolve blocking
										problems.
									{:else}Lock the protocol, then launch.{/if}
								</Card.Description>
							</Card.Header>
							<Card.Content class="flex flex-wrap gap-2">
								<Button size="sm" href="/analysis/studies/new?id={study.id}&step={setupStep}"
									>Continue setup<ArrowRight /></Button
								>
								{#if study.actions.preflight && hasTargetSpec(study.target_spec)}
									<Button variant="outline" size="sm" onclick={doPreflight} disabled={busy !== ''}>
										{#if busy === 'preflight'}<LoaderCircle
												class="animate-spin motion-reduce:animate-none"
											/>{/if}Run preflight
									</Button>
								{/if}
							</Card.Content>
						</Card.Root>
						{#if preflightReport}
							<PreflightView
								report={preflightReport}
								onrerun={doPreflight}
								rerunning={busy === 'preflight'}
								{outcomeLabels}
							/>
						{/if}
					{:else if study.status === 'locked'}
						<LaunchPanel
							{study}
							onchange={(s) => (study = s)}
							onlaunched={(s) => {
								study = s;
								void load(true);
							}}
						/>
					{:else if progress}
						<StudyProgressPanel {progress} onresume={doResume} resuming={busy === 'resume'} />
					{:else}
						<LoadingState label="loading progress" />
					{/if}

					<!-- Summary -->
					<Card.Root>
						<Card.Header><Card.Title>Study design</Card.Title></Card.Header>
						<Card.Content class="space-y-4">
							<dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
								<div>
									<dt class="section-label">Targets</dt>
									<dd>{targetSpecSummary(study.target_spec, options)}</dd>
								</div>
								<div>
									<dt class="section-label">Profile</dt>
									<dd>
										{#if study.profile}
											<a
												class="hover:text-primary hover:underline"
												href="/analysis/profiles/{study.profile.id}">{profileLabel(study)}</a
											>
										{:else}none{/if}
									</dd>
								</div>
								<div>
									<dt class="section-label">Grouping</dt>
									<dd>
										{factorList(study.analysis_design.group_by)}
										{#if study.analysis_design.block_by}· blocked by {DESIGN_FACTOR_LABELS[
												study.analysis_design.block_by
											]}{/if}
									</dd>
								</div>
								<div>
									<dt class="section-label">Primary outcome</dt>
									<dd>
										{outcomeLabels[study.analysis_design.primary_outcome] ??
											study.analysis_design.primary_outcome}
									</dd>
								</div>
								<div>
									<dt class="section-label">Significance · corrections</dt>
									<dd>
										α = {study.analysis_design.alpha} · {CORRECTION_LABELS[
											study.analysis_design.correction
										]} /
										{CORRECTION_LABELS[study.analysis_design.exploratory_correction]}
									</dd>
								</div>
								<div>
									<dt class="section-label">Preflight</dt>
									<dd>
										{#if study.preflight_summary}
											{study.preflight_summary.eligible ?? '—'} of {study.preflight_summary
												.targets ?? '—'} eligible ·
											{study.preflight_summary.blocking} blocking · {study.preflight_summary
												.warnings} warn · est. AI
											{fmtUsd(study.preflight_summary.ai_cost_usd)}
										{:else}not run{/if}
									</dd>
								</div>
							</dl>
							{#if study.research_questions.length > 0}
								<div class="space-y-2">
									<h3 class="section-label">Research questions</h3>
									<ol class="space-y-1.5 text-sm">
										{#each study.research_questions as rq (rq.id)}
											<li class="flex flex-wrap items-baseline gap-2">
												<span class="mono-tag">{rq.id}</span>
												<StatusBadge
													status={rq.kind === 'confirmatory' ? 'locked' : 'draft'}
													label={rq.kind}
												/>
												<span class="min-w-0 flex-1">{rq.text}</span>
											</li>
										{/each}
									</ol>
								</div>
							{/if}
							<p class="text-[11px] text-muted-foreground">
								Created {fmtDateTime(study.created_at)}
								{#if study.locked_at}· locked {fmtDateTime(study.locked_at)}{/if}
								{#if study.launched_at}· launched {fmtDateTime(study.launched_at)}{/if}
								{#if study.completed_at}· finished {fmtDateTime(study.completed_at)}{/if}
							</p>
						</Card.Content>
					</Card.Root>
				{/if}
			</Tabs.Content>

			<!-- Protocol -->
			<Tabs.Content value="protocol" class="space-y-4">
				{#if tab === 'protocol'}
					{#if !study.protocol_hash}
						<EmptyState
							icon={ScrollText}
							title="Not locked yet"
							description="The protocol is frozen (and hashed) when you lock the study."
						/>
					{:else if protocolError}
						<ErrorAlert message={protocolError} onretry={() => (protocolError = '')} />
					{:else if !protocol}
						<LoadingState label="loading protocol" />
					{:else}
						<div class="flex flex-wrap justify-end gap-2">
							<Button
								variant="outline"
								size="sm"
								onclick={() => download(() => downloadStudyProtocolMarkdown(study!.id))}
							>
								<FileDown />protocol.md
							</Button>
							<Button
								variant="outline"
								size="sm"
								onclick={() =>
									protocol && saveJson(protocol, `${studyFileStem(studyId)}-protocol.json`)}
							>
								<FileJson />protocol.json
							</Button>
						</div>
						<ProtocolView envelope={protocol} {options} />
					{/if}
				{/if}
			</Tabs.Content>

			<!-- Results -->
			<Tabs.Content value="results" class="space-y-4">
				{#if tab === 'results'}
					{#if study.status === 'draft' || study.status === 'locked'}
						<EmptyState
							icon={ChartColumn}
							title="No results yet"
							description="Results are reported after the study has been launched and its runs have finished."
						/>
					{:else if reportError}
						<ErrorAlert message={reportError} onretry={fetchReport} />
					{:else if reportEnv?.status === 'ready' && reportEnv.report}
						<div class="flex flex-wrap items-center justify-between gap-2">
							<p class="text-xs text-muted-foreground">
								Generated {fmtDateTime(reportEnv.generated_at)} from stored data only (schema-versioned,
								sha256-linked to the protocol).
							</p>
							<div class="flex flex-wrap gap-2">
								<Button
									variant="outline"
									size="sm"
									onclick={() => download(() => downloadStudyReportMarkdown(study!.id))}
								>
									<FileDown />report.md
								</Button>
								<Button
									variant="outline"
									size="sm"
									onclick={() =>
										reportEnv?.report &&
										saveJson(reportEnv.report, `${studyFileStem(studyId)}-report.json`)}
								>
									<FileJson />report.json
								</Button>
							</div>
						</div>
						<ReportView
							report={reportEnv.report}
							verified={reportEnv.verified}
							stale={reportEnv.stale}
							studyId={study.id}
						/>
					{:else if reportEnv?.status === 'generating' || reportWaiting}
						<Card.Root>
							<Card.Content class="flex flex-col items-center gap-3 py-10 text-center">
								<LoaderCircle
									class="h-6 w-6 animate-spin text-primary motion-reduce:animate-none"
									aria-hidden="true"
								/>
								<p class="text-sm font-medium">Generating the report…</p>
								<p class="max-w-md text-xs text-muted-foreground">
									Statistics run in the background over the stored measurements (bootstrap resamples
									can take a minute).
									{#if reportEnv?.requested_at}Requested {fmtDateTime(reportEnv.requested_at)}.{/if}
								</p>
							</Card.Content>
						</Card.Root>
					{:else if reportEnv?.status === 'failed'}
						<ErrorAlert
							title="Report generation failed"
							message={reportEnv.error || 'Unknown error.'}
							onretry={study.actions.report ? generateReport : undefined}
						/>
					{:else if reportMissing || !reportEnv}
						{#if reportLoading}
							<LoadingState label="loading report" />
						{:else if study.actions.report}
							<EmptyState
								icon={FileChartColumn}
								title="No report yet"
								description="Build the post-run report: data quality first, then descriptives, inferential tests, AI reliability, tool overlap, cost and provenance."
							>
								{#snippet action()}
									<Button size="sm" onclick={generateReport} disabled={busy !== ''}>
										{#if busy === 'report'}<LoaderCircle
												class="animate-spin motion-reduce:animate-none"
											/>{:else}<FileChartColumn />{/if}Generate report
									</Button>
								{/snippet}
							</EmptyState>
						{:else}
							<EmptyState
								icon={ChartColumn}
								title="Runs still in progress"
								description="The report can be generated once every run has finished (or the study is cancelled). Progress is on the Overview tab."
							>
								{#snippet action()}
									<Button variant="outline" size="sm" onclick={() => setTab('overview')}
										>View progress</Button
									>
								{/snippet}
							</EmptyState>
						{/if}
					{/if}
				{/if}
			</Tabs.Content>

			<!-- Runs -->
			<Tabs.Content value="runs" class="space-y-3">
				{#if tab === 'runs'}
					{#if launched}
						<StudyRunsTable studyId={study.id} {refreshKey} />
					{:else}
						<EmptyState
							icon={Microscope}
							title="No runs yet"
							description="Launching the study submits one analysis run per eligible target."
						/>
					{/if}
				{/if}
			</Tabs.Content>

			<!-- Settings -->
			<Tabs.Content value="settings" class="space-y-4">
				{#if tab === 'settings'}
					{#if study.status === 'draft'}
						<Card.Root>
							<Card.Header>
								<Card.Title>Edit the draft</Card.Title>
								<Card.Description
									>Changes are made in the setup steps; any change discards the stored preflight.</Card.Description
								>
							</Card.Header>
							<Card.Content class="flex flex-wrap gap-2">
								<Button
									variant="outline"
									size="sm"
									href="/analysis/studies/new?id={study.id}&step=question"
									><Pencil />Question</Button
								>
								<Button
									variant="outline"
									size="sm"
									href="/analysis/studies/new?id={study.id}&step=targets"><Pencil />Targets</Button
								>
								<Button
									variant="outline"
									size="sm"
									href="/analysis/studies/new?id={study.id}&step=method"><Pencil />Method</Button
								>
							</Card.Content>
						</Card.Root>
					{:else if isFrozenPlan(study.plan)}
						<PlanView plan={study.plan} planHash={study.plan_hash} />
					{/if}

					<Card.Root class="border-destructive/30">
						<Card.Header>
							<Card.Title>Danger zone</Card.Title>
							<Card.Description
								>Cancelling stops active runs and keeps finished ones; deleting removes the study
								with its runs, findings and artifacts.</Card.Description
							>
						</Card.Header>
						<Card.Content class="flex flex-wrap gap-2">
							{#if study.actions.cancel}
								<Button
									variant="outline"
									size="sm"
									onclick={() => (cancelOpen = true)}
									disabled={busy !== ''}><CircleStop />Cancel study</Button
								>
							{/if}
							{#if study.actions.delete}
								<Button
									variant="destructive"
									size="sm"
									onclick={() => (deleteOpen = true)}
									disabled={busy !== ''}><Trash2 />Delete study</Button
								>
							{:else}
								<p class="text-xs text-muted-foreground">
									A running study cannot be deleted; cancel it first.
								</p>
							{/if}
						</Card.Content>
					</Card.Root>
				{/if}
			</Tabs.Content>
		</Tabs.Root>
	{/if}
</div>

<ConfirmDialog
	bind:open={cancelOpen}
	title="Cancel this study?"
	description={study?.status === 'running'
		? `Active runs are cancelled (${buckets.active} queued or running); finished runs are kept and can still be reported.`
		: 'The study is marked cancelled and cannot be launched any more.'}
	confirmLabel="Cancel study"
	cancelLabel="Keep running"
	destructive
	busy={busy === 'cancel'}
	onConfirm={doCancel}
/>
<ConfirmDialog
	bind:open={retryOpen}
	title="Retry failed runs?"
	description={`${retryable} failed, partial or cancelled run(s) are requeued under the same frozen plan. A newer report becomes necessary afterwards.`}
	confirmLabel="Retry runs"
	busy={busy === 'retry'}
	onConfirm={doRetry}
/>
<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete this study?"
	description={`The study, its ${buckets.total} run(s), their findings and artifacts are deleted permanently.`}
	confirmLabel="Delete study"
	destructive
	busy={busy === 'delete'}
	onConfirm={doDelete}
/>
