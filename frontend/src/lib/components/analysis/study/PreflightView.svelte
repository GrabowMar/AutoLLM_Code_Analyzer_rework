<script lang="ts">
	/**
	 * PreflightView — the pre-run report of a study: blocking problems,
	 * warnings and info first, then the planned workload (KPIs and cost),
	 * analyzer image, tool readiness (version + self-test evidence), the
	 * targets × tools applicability matrix, AI reviewer checks,
	 * comparability of the targets and the design (groups × blocks).
	 *
	 * Remedies are offered in place: build the analyzer image, run the tool
	 * self-test; both poll until done and then ask the parent to re-run the
	 * preflight (`onrerun`).
	 *
	 * Props: `report: PreflightReport`, `onrerun()` (re-run the preflight;
	 * omitted = read-only stored report), `rerunning`, `outcomeLabels`
	 * (outcome key → label).
	 */
	import { onDestroy } from 'svelte';
	import {
		buildAnalyzerImage,
		getAnalysisRun,
		getAnalyzerImage,
		isRunActive,
		startSelftest,
		type PreflightIssue,
		type PreflightReport
	} from '$lib/api/analysis';
	import { formatApiError } from '$lib/api/core';
	import { OpenRouterKeyBanner } from '$lib/components/generator';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import HeatmapGrid from '$lib/components/charts/HeatmapGrid.svelte';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Hammer from '@lucide/svelte/icons/hammer';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import IssueList from './IssueList.svelte';
	import StatusMatrix from './StatusMatrix.svelte';
	import TargetTable from './TargetTable.svelte';
	import NaValue from '../NaValue.svelte';
	import Hint from '../Hint.svelte';
	import {
		DESIGN_FACTOR_LABELS,
		CORRECTION_LABELS,
		factorList,
		fmtBytes,
		fmtDateTime,
		fmtInt,
		fmtPct,
		fmtSeconds,
		fmtUsd,
		humanize,
		shortHash
	} from './format';

	interface Props {
		report: PreflightReport;
		onrerun?: () => void | Promise<void>;
		rerunning?: boolean;
		outcomeLabels?: Record<string, string>;
	}

	let { report, onrerun, rerunning = false, outcomeLabels = {} }: Props = $props();

	const est = $derived(report.estimates);
	const reviewer = $derived(report.reviewer);
	const blockingCount = $derived(report.blocking.length);

	// ── Image build ─────────────────────────────────────────────────────
	let imageBusy = $state(false);
	let imageMessage = $state('');
	let imageError = $state('');
	let pollTimer: ReturnType<typeof setTimeout> | null = null;
	let disposed = false;

	onDestroy(() => {
		disposed = true;
		if (pollTimer) clearTimeout(pollTimer);
	});

	function wait(ms: number): Promise<void> {
		return new Promise((resolve) => {
			pollTimer = setTimeout(resolve, ms);
		});
	}

	async function buildImage() {
		imageBusy = true;
		imageError = '';
		imageMessage = 'Starting the image build…';
		try {
			let status = await buildAnalyzerImage();
			const started = Date.now();
			while (!disposed && !status.built) {
				if (!status.daemon_ok)
					throw new Error(status.error || 'The analysis Docker daemon is unreachable.');
				if (!status.building && !status.build_requested && status.error)
					throw new Error(status.error);
				imageMessage = `Building ${status.tag}… ${fmtSeconds((Date.now() - started) / 1000)}`;
				await wait(5000);
				status = await getAnalyzerImage({ refresh: true });
			}
			if (disposed) return;
			imageMessage = 'Image built. Re-running the preflight…';
			await onrerun?.();
			imageMessage = '';
		} catch (e) {
			imageError = formatApiError(e, 'The image build failed.');
			imageMessage = '';
		} finally {
			imageBusy = false;
		}
	}

	// ── Self-test ───────────────────────────────────────────────────────
	let selftestBusy = $state(false);
	let selftestRunId = $state('');
	let selftestMessage = $state('');
	let selftestError = $state('');

	async function runSelftest(tools?: string[]) {
		selftestBusy = true;
		selftestError = '';
		selftestMessage = 'Queuing the self-test…';
		try {
			const run = await startSelftest(tools);
			selftestRunId = run.id;
			let status: string = run.status;
			while (!disposed && isRunActive(status)) {
				selftestMessage = 'Self-test running on the canary fixtures…';
				await wait(4000);
				status = (await getAnalysisRun(run.id)).status;
			}
			if (disposed) return;
			selftestMessage = `Self-test ${status}. Re-running the preflight…`;
			await onrerun?.();
			selftestMessage = '';
		} catch (e) {
			selftestError = formatApiError(e, 'Could not run the self-test.');
			selftestMessage = '';
		} finally {
			selftestBusy = false;
		}
	}

	// ── Targets / applicability ─────────────────────────────────────────
	let targetFilter = $state<'all' | 'eligible' | 'excluded'>('all');
	const eligibleTargets = $derived(report.targets.filter((t) => t.eligible));
	const shownTargets = $derived(
		targetFilter === 'eligible'
			? eligibleTargets
			: targetFilter === 'excluded'
				? report.targets.filter((t) => !t.eligible)
				: report.targets
	);
	// Plan order (the stored report's JSON keys come back sorted), AI reviewer last.
	const applicabilityTools = $derived.by(() => {
		const keys = Object.keys(report.applicability);
		const ordered = report.tools.map((t) => t.slug).filter((s) => keys.includes(s));
		return [...ordered, ...keys.filter((k) => !ordered.includes(k))];
	});
	const sortedEligible = $derived(
		[...eligibleTargets].sort(
			(a, b) => (a.group || '').localeCompare(b.group || '') || a.label.localeCompare(b.label)
		)
	);
	const matrixRowsSorted = $derived(
		sortedEligible.map((t, i) => ({
			key: t.job_id ?? `inline-${i}`,
			label: t.label,
			group: t.group,
			href: t.job_id ? `/applications/${t.job_id}` : null
		}))
	);
	const matrixCells = $derived(
		sortedEligible.map((t) => applicabilityTools.map((slug) => t.tools[slug] ?? 'unavailable'))
	);
	const matrixReasons = $derived(
		sortedEligible.map((t) =>
			applicabilityTools.map((slug) => {
				const n = t.tool_files[slug];
				return typeof n === 'number' ? `${n} file${n === 1 ? '' : 's'} in scope` : '';
			})
		)
	);

	// ── Design ──────────────────────────────────────────────────────────
	const designGroups = $derived(Object.keys(report.design.groups));
	const designBlocks = $derived(Object.keys(report.design.blocks));
	const designCells = $derived(
		designGroups.map((g) => designBlocks.map((b) => report.design.cells[g]?.[b] ?? 0))
	);

	const IDENTITY_LABELS: Record<string, string> = {
		prompt_hash: 'Prompt',
		bundle_key: 'Bundle',
		stack_slug: 'Stack',
		mode: 'Mode',
		harness: 'Harness'
	};

	function check(ok: boolean | null | undefined) {
		return ok === true ? 'yes' : ok === false ? 'no' : 'unknown';
	}
</script>

{#snippet bool(ok: boolean | null | undefined, yes: string, no: string)}
	<span class="inline-flex items-center gap-1.5">
		{#if ok === true}
			<CircleCheck class="h-4 w-4 text-success-fg" aria-hidden="true" />{yes}
		{:else if ok === false}
			<CircleX class="h-4 w-4 text-danger-fg" aria-hidden="true" />{no}
		{:else}
			<NaValue />
		{/if}
	</span>
{/snippet}

{#snippet issueAction(issue: PreflightIssue)}
	{#if issue.code === 'image_missing' && onrerun}
		<Button size="sm" variant="outline" onclick={buildImage} disabled={imageBusy}>
			{#if imageBusy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Hammer
				/>{/if}
			Build image
		</Button>
	{:else if (issue.code === 'canary_unknown' || issue.code === 'canary_failed') && onrerun}
		<Button
			size="sm"
			variant="outline"
			onclick={() => runSelftest(issue.tools)}
			disabled={selftestBusy}
		>
			{#if selftestBusy}<LoaderCircle
					class="animate-spin motion-reduce:animate-none"
				/>{:else}<FlaskConical />{/if}
			Run self-test
		</Button>
	{:else if issue.code === 'missing_api_key' || issue.code === 'reviewer_key_missing'}
		<Button size="sm" variant="outline" href="/users/settings#credentials">Add key</Button>
	{/if}
{/snippet}

<div class="space-y-4">
	<!-- Verdict -->
	<div
		class="flex flex-wrap items-center justify-between gap-3 rounded-md border px-4 py-3 {blockingCount >
		0
			? 'border-destructive/40 bg-destructive/5'
			: report.warnings.length > 0
				? 'border-warning/40 bg-warning/5'
				: 'border-success/40 bg-success/5'}"
		role="status"
	>
		<div class="min-w-0 space-y-0.5">
			<p class="text-sm font-semibold">
				{#if blockingCount > 0}
					{blockingCount} blocking problem{blockingCount === 1 ? '' : 's'}: the protocol cannot be
					locked yet.
				{:else if report.warnings.length > 0}
					Ready to lock, with {report.warnings.length} warning{report.warnings.length === 1
						? ''
						: 's'} to review.
				{:else}
					Ready to lock: no blocking problem and no warning.
				{/if}
			</p>
			<p class="text-xs text-muted-foreground">
				Generated {fmtDateTime(report.generated_at)} · plan
				<span class="font-mono" title={report.plan_hash}>#{shortHash(report.plan_hash)}</span>
			</p>
		</div>
		{#if onrerun}
			<Button size="sm" variant="outline" onclick={() => onrerun?.()} disabled={rerunning}>
				{#if rerunning}<LoaderCircle
						class="animate-spin motion-reduce:animate-none"
					/>{:else}<RefreshCw />{/if}
				Re-run preflight
			</Button>
		{/if}
	</div>

	{#if imageMessage || imageError || selftestMessage || selftestError}
		<div class="space-y-2" aria-live="polite">
			{#if imageMessage}
				<p class="flex items-center gap-2 text-sm text-muted-foreground">
					<LoaderCircle
						class="h-4 w-4 animate-spin motion-reduce:animate-none"
						aria-hidden="true"
					/>{imageMessage}
				</p>
			{/if}
			{#if imageError}<ErrorAlert
					title="Image build"
					message={imageError}
					onretry={buildImage}
				/>{/if}
			{#if selftestMessage}
				<p class="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
					<LoaderCircle
						class="h-4 w-4 animate-spin motion-reduce:animate-none"
						aria-hidden="true"
					/>{selftestMessage}
					{#if selftestRunId}
						<a class="text-primary hover:underline" href="/analysis/{selftestRunId}"
							>Open self-test run</a
						>
					{/if}
				</p>
			{/if}
			{#if selftestError}<ErrorAlert
					title="Self-test"
					message={selftestError}
					onretry={() => runSelftest()}
				/>{/if}
		</div>
	{/if}

	<!-- Problems -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Checks</Card.Title>
			<Card.Description
				>Blocking problems prevent locking; warnings are recorded in the protocol.</Card.Description
			>
		</Card.Header>
		<Card.Content class="space-y-4">
			<IssueList
				issues={report.blocking}
				tone="danger"
				title="Blocking"
				action={issueAction}
				empty="No blocking problem."
			/>
			<IssueList issues={report.warnings} tone="warning" title="Warnings" action={issueAction} />
			<IssueList issues={report.info} tone="info" title="Info" action={issueAction} />
		</Card.Content>
	</Card.Root>

	<!-- Workload -->
	<div class="grid grid-cols-2 gap-3 md:grid-cols-4">
		<div class="kpi-card">
			<span class="kpi-label">Targets</span>
			<span class="kpi-value">{fmtInt(est.eligible)}</span>
			<span class="text-[11px] text-muted-foreground"
				>eligible of {fmtInt(est.targets)} · {fmtInt(est.excluded)} excluded</span
			>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Tool executions</span>
			<span class="kpi-value">{fmtInt(est.tool_executions)}</span>
			<span class="text-[11px] text-muted-foreground"
				>{fmtInt(est.files)} files · {fmtInt(est.sloc_measured)} measured SLOC</span
			>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">AI review</span>
			<span class="kpi-value">{fmtInt(est.ai_calls)}</span>
			<span class="text-[11px] text-muted-foreground"
				>calls · {fmtInt(est.ai_reviews)} reviews × samples = {fmtInt(est.ai_samples)}</span
			>
		</div>
		<div class="kpi-card">
			<span class="kpi-label">Est. AI cost</span>
			<span class="kpi-value">
				{#if est.ai_cost_usd === null}<NaValue
						reason="No AI reviewer, or the model has no catalog price."
					/>{:else}{fmtUsd(est.ai_cost_usd)}{/if}
			</span>
			<span class="text-[11px] text-muted-foreground">
				range {fmtUsd(est.ai_cost_usd_low)} – {fmtUsd(est.ai_cost_usd_high)} · ≈
				{fmtSeconds(est.wall_seconds)} wall time
			</span>
		</div>
	</div>

	<div class="grid gap-4 xl:grid-cols-2">
		<!-- Image -->
		<Card.Root>
			<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2">
				<div class="space-y-1">
					<Card.Title>Analyzer image</Card.Title>
					<Card.Description
						>Tool versions are read from this image and frozen into the plan.</Card.Description
					>
				</div>
				<StatusBadge
					status={report.image.built
						? 'ready'
						: report.image.building
							? 'building'
							: report.image.daemon_ok
								? 'image_missing'
								: 'error'}
					label={report.image.built
						? 'built'
						: report.image.building
							? 'building'
							: report.image.daemon_ok
								? 'not built'
								: 'daemon down'}
				/>
			</Card.Header>
			<Card.Content>
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					<dt class="text-muted-foreground">Tag</dt>
					<dd class="truncate font-mono text-xs">{report.image.tag ?? '—'}</dd>
					{#if report.image.image_id}
						<dt class="text-muted-foreground">Image id</dt>
						<dd class="truncate font-mono text-xs" title={report.image.image_id}>
							{shortHash(report.image.image_id, 19)}
						</dd>
					{/if}
					<dt class="text-muted-foreground">Built</dt>
					<dd>
						{fmtDateTime(report.image.manifest?.built_at ?? report.image.created ?? null)}
					</dd>
					{#if report.image.manifest}
						<dt class="text-muted-foreground">Release cutoff</dt>
						<dd>{report.image.manifest.release_cutoff}</dd>
						<dt class="text-muted-foreground">Runtime</dt>
						<dd class="text-xs">
							Python {report.image.manifest.runtime.python} · Node {report.image.manifest.runtime
								.node}
						</dd>
					{/if}
					{#if report.image.size_bytes}
						<dt class="text-muted-foreground">Size</dt>
						<dd>{fmtBytes(report.image.size_bytes)}</dd>
					{/if}
				</dl>
				{#if report.image.error}<p class="mt-2 text-xs text-danger-fg">
						{report.image.error}
					</p>{/if}
				{#if !report.image.built && report.image.daemon_ok && onrerun}
					<Button class="mt-3" size="sm" onclick={buildImage} disabled={imageBusy}>
						{#if imageBusy}<LoaderCircle
								class="animate-spin motion-reduce:animate-none"
							/>{:else}<Hammer />{/if}
						Build image
					</Button>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Reviewer -->
		<Card.Root>
			<Card.Header>
				<Card.Title>AI reviewer</Card.Title>
				<Card.Description
					>Checked against the model catalog and your OpenRouter key.</Card.Description
				>
			</Card.Header>
			<Card.Content class="space-y-3">
				{#if !reviewer.enabled}
					<p class="text-sm text-muted-foreground">
						The profile has no AI reviewer: static tools only.
					</p>
				{:else}
					<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
						<dt class="text-muted-foreground">Model</dt>
						<dd class="min-w-0">
							<span class="block truncate font-mono text-xs" title={reviewer.model_canonical_slug}
								>{reviewer.model}</span
							>
						</dd>
						<dt class="text-muted-foreground">Engine</dt>
						<dd>
							{reviewer.engine === 'agent'
								? `Agent (${reviewer.harness ?? '—'} ${reviewer.harness_version})`
								: 'Single-shot'}
							· k = {reviewer.samples} · {reviewer.prompt_version}
							{#if reviewer.required}<span class="text-[11px] text-muted-foreground"
									>(required)</span
								>{/if}
						</dd>
						<dt class="text-muted-foreground">In catalog</dt>
						<dd>{@render bool(reviewer.model_found, 'found', 'not found')}</dd>
						<dt class="text-muted-foreground">Structured outputs</dt>
						<dd>
							{@render bool(
								reviewer.structured_outputs,
								'strict JSON schema',
								'JSON mode + local validation'
							)}
						</dd>
						{#if reviewer.engine === 'agent'}
							<dt class="text-muted-foreground">Tool calling</dt>
							<dd>
								{@render bool(reviewer.tool_calling, 'supported', 'not supported')}
							</dd>
						{/if}
						<dt class="text-muted-foreground">API key</dt>
						<dd>
							{@render bool(reviewer.key_present, 'available', 'missing')}
						</dd>
						<dt class="text-muted-foreground">Price</dt>
						<dd class="text-xs">
							{#if typeof reviewer.pricing.input_per_token === 'number' && typeof reviewer.pricing.output_per_token === 'number'}
								${(reviewer.pricing.input_per_token * 1e6).toFixed(3)} in · ${(
									reviewer.pricing.output_per_token * 1e6
								).toFixed(3)} out per 1M tokens
							{:else}
								<NaValue reason="No catalog price for this model." />
							{/if}
						</dd>
						<dt class="text-muted-foreground">Budget / sample</dt>
						<dd class="text-xs">
							{fmtUsd(reviewer.budget.max_cost_usd ?? null)} · {reviewer.budget.max_llm_requests ??
								'—'} calls ·
							{fmtSeconds(reviewer.budget.wall_clock_seconds ?? null)}
						</dd>
						<dt class="text-muted-foreground">Estimate</dt>
						<dd class="text-xs">
							<span class="font-medium">{fmtUsd(reviewer.cost_estimate.expected)}</span>
							(low {fmtUsd(reviewer.cost_estimate.low)}, high {fmtUsd(reviewer.cost_estimate.high)})
							·
							{fmtUsd(reviewer.cost_estimate.per_target)} per target · {reviewer.cost_estimate
								.calls} calls
						</dd>
					</dl>
					<OpenRouterKeyBanner hideWhenOk />
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Tool readiness -->
	<Card.Root>
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2">
			<div class="space-y-1">
				<Card.Title>Tool readiness</Card.Title>
				<Card.Description>
					Version from the image manifest and the latest self-test (canary) verdict on this image. A
					tool without a passing self-test has no proof it works.
				</Card.Description>
			</div>
			{#if onrerun}
				<Button size="sm" variant="outline" onclick={() => runSelftest()} disabled={selftestBusy}>
					{#if selftestBusy}<LoaderCircle
							class="animate-spin motion-reduce:animate-none"
						/>{:else}<FlaskConical />{/if}
					Run self-test
				</Button>
			{/if}
		</Card.Header>
		<Card.Content class="px-0">
			<div class="table-scroll-wrapper">
				<table class="table-card-mobile">
					<thead>
						<tr>
							<th scope="col">Tool</th>
							<th scope="col">Category</th>
							<th scope="col">Version</th>
							<th scope="col">Config</th>
							<th scope="col">Self-test</th>
							<th scope="col">Readiness</th>
						</tr>
					</thead>
					<tbody>
						{#each report.tools as tool (tool.slug)}
							<tr>
								<td data-label="Tool">
									<span class="font-medium">{tool.name || tool.slug}</span>
									{#if tool.required}<span class="ml-1 text-[11px] text-muted-foreground"
											>required</span
										>{/if}
								</td>
								<td data-label="Category" class="text-muted-foreground"
									>{tool.category || '—'} · {tool.kind || '—'}</td
								>
								<td data-label="Version">
									<span class="font-mono text-xs">{tool.version || '—'}</span>
									{#if tool.version_source !== 'image_manifest'}
										<Hint
											underline={false}
											text="Not read from the built image: the version is what the catalog expects, not verified."
										>
											<span class="text-[11px] text-warning-fg">unverified</span>
										</Hint>
									{/if}
								</td>
								<td
									data-label="Config"
									class="font-mono text-xs text-muted-foreground"
									title={tool.config_hash}
								>
									#{shortHash(tool.config_hash, 8)}
								</td>
								<td data-label="Self-test">
									<span class="inline-flex flex-wrap items-center gap-1.5">
										<StatusBadge status={tool.canary.verdict} />
										{#if tool.canary.run_id}
											<a
												class="text-[11px] text-muted-foreground hover:text-primary hover:underline"
												href="/analysis/{tool.canary.run_id}"
											>
												{fmtDateTime(tool.canary.evaluated_at)}
											</a>
										{/if}
									</span>
									{#if tool.canary.missing.length > 0}
										<span class="mt-1 block text-[11px] text-danger-fg">
											missed: {tool.canary.missing.map((m) => m.rule).join(', ')}
										</span>
									{/if}
									{#if tool.canary.unexpected_in_clean.length > 0}
										<span class="mt-1 block text-[11px] text-danger-fg">
											{tool.canary.unexpected_in_clean.length} finding(s) on clean code
										</span>
									{/if}
								</td>
								<td data-label="Readiness"><StatusBadge status={tool.status} /></td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</Card.Content>
	</Card.Root>

	<!-- Applicability -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Applicability</Card.Title>
			<Card.Description>
				Which tool will scan files of each eligible target. "Not applicable" (no files in scope) is
				recorded as such and never read as a clean result.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			<div class="flex flex-wrap gap-1.5 text-[11px]">
				{#each applicabilityTools as slug (slug)}
					{@const a = report.applicability[slug]}
					<span class="mono-tag gap-1" class:text-warning-fg={a.run === 0}>
						{slug}
						<span class="text-muted-foreground">{a.run} run · {a.not_applicable} n/a</span>
					</span>
				{/each}
			</div>
			<StatusMatrix
				rows={matrixRowsSorted}
				columns={applicabilityTools.map((slug) => ({ key: slug, label: slug }))}
				cells={matrixCells}
				reasons={matrixReasons}
				tones={{
					run: 'success',
					not_applicable: 'neutral',
					unavailable: 'warning',
					error: 'danger'
				}}
				labels={{
					run: 'Will run',
					not_applicable: 'Not applicable',
					unavailable: 'Unavailable',
					error: 'Error'
				}}
				label="Tool applicability per eligible target"
			/>
		</Card.Content>
	</Card.Root>

	<div class="grid gap-4 xl:grid-cols-2">
		<!-- Comparability -->
		<Card.Root>
			<Card.Header>
				<Card.Title>Comparability</Card.Title>
				<Card.Description>
					Identity of the eligible targets. Mixed prompts, bundles or stacks are confounders unless
					they are the studied factor.
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-2">
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
					{#each Object.entries(report.comparability.values) as [key, values] (key)}
						{@const mixed =
							!!report.comparability.mixed[key as keyof typeof report.comparability.mixed]}
						<dt class="text-muted-foreground">
							{IDENTITY_LABELS[key] ?? humanize(key)}
						</dt>
						<dd class="min-w-0">
							{#if values.length === 0}
								<span class="text-muted-foreground">none</span>
							{:else}
								<span class="flex flex-wrap items-center gap-1">
									{#each values as v (v)}<span class="mono-tag">{v}</span>{/each}
									{#if mixed}<StatusBadge status="warning" label="mixed" />{/if}
								</span>
							{/if}
						</dd>
					{/each}
				</dl>
				{#each report.comparability.notes as note, i (i)}
					<p class="text-xs text-warning-fg">{note.detail}</p>
				{/each}
			</Card.Content>
		</Card.Root>

		<!-- Design -->
		<Card.Root>
			<Card.Header>
				<Card.Title>Design</Card.Title>
				<Card.Description
					>Eligible targets per group × block cell, as the report will analyse them.</Card.Description
				>
			</Card.Header>
			<Card.Content class="space-y-3">
				<dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-3">
					<div>
						<dt class="section-label">Group by</dt>
						<dd>{factorList(report.design.group_by)}</dd>
					</div>
					<div>
						<dt class="section-label">Block by</dt>
						<dd>
							{report.design.block_by ? DESIGN_FACTOR_LABELS[report.design.block_by] : 'none'}
						</dd>
					</div>
					<div>
						<dt class="section-label">Mode</dt>
						<dd>{report.design.mode}</dd>
					</div>
					<div>
						<dt class="section-label">Groups</dt>
						<dd>
							{report.design.n_groups} (min n {report.design.min_group_size})
						</dd>
					</div>
					<div>
						<dt class="section-label">Complete / balanced</dt>
						<dd>
							{check(report.design.complete_blocks)} / {check(report.design.balanced)}
						</dd>
					</div>
					<div>
						<dt class="section-label">Replicates</dt>
						<dd>
							{report.design.replicates
								? `${report.design.replicates.min}–${report.design.replicates.max}`
								: '—'}
						</dd>
					</div>
					<div class="col-span-2 sm:col-span-3">
						<dt class="section-label">Primary outcome · significance · correction</dt>
						<dd>
							{outcomeLabels[report.design.primary_outcome] ?? report.design.primary_outcome} · α = {report
								.design.alpha} ·
							{CORRECTION_LABELS[report.design.correction] ?? report.design.correction}
						</dd>
					</div>
				</dl>
				<p class="text-xs text-muted-foreground">{report.design.reason}</p>
				{#if designGroups.length > 0 && designBlocks.length > 0}
					<HeatmapGrid
						rows={designGroups}
						columns={designBlocks}
						values={designCells}
						format={(v) => String(v)}
						rowHeader="Group / block"
						legendLabel="Targets"
						label="Eligible targets per group and block"
					/>
				{:else if designGroups.length > 0}
					<ul class="flex flex-wrap gap-1.5 text-xs">
						{#each Object.entries(report.design.groups) as [g, n] (g)}<li class="mono-tag gap-1">
								{g}<span class="text-muted-foreground">{n}</span>
							</li>{/each}
					</ul>
				{/if}
				{#each report.design.notes as note, i (i)}<p class="text-xs text-warning-fg">
						{note}
					</p>{/each}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Targets -->
	<Card.Root>
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2">
			<div class="space-y-1">
				<Card.Title>Targets</Card.Title>
				<Card.Description>
					{report.targets.length} resolved · {eligibleTargets.length} eligible ({fmtPct(
						report.targets.length ? eligibleTargets.length / report.targets.length : null
					)}).
				</Card.Description>
			</div>
			<Segmented
				size="sm"
				aria-label="Show targets"
				bind:value={targetFilter}
				items={[
					{ value: 'all', label: 'All', badge: String(report.targets.length) },
					{
						value: 'eligible',
						label: 'Eligible',
						badge: String(eligibleTargets.length)
					},
					{
						value: 'excluded',
						label: 'Excluded',
						badge: String(report.targets.length - eligibleTargets.length)
					}
				]}
			/>
		</Card.Header>
		<Card.Content class="px-0">
			<TargetTable
				targets={shownTargets}
				showGroup
				showCost={reviewer.enabled}
				empty="No target in this view."
			/>
		</Card.Content>
	</Card.Root>

	{#if report.rules}
		<p class="text-[11px] text-muted-foreground">
			Exclusion rules applied: invalid {report.rules.exclude_invalid ? 'excluded' : 'kept'} · empty output
			{report.rules.exclude_empty_output ? 'excluded' : 'kept'} · truncated {report.rules
				.exclude_truncated
				? 'excluded'
				: 'kept'}
			· functional result {report.rules.require_functional ? 'required' : 'not required'} · min measured
			SLOC
			{report.rules.min_measured_sloc}.
			<a
				class="inline-flex items-center gap-0.5 hover:underline"
				href="/docs"
				target="_blank"
				rel="noopener"
			>
				Methodology <ExternalLink class="h-3 w-3" aria-hidden="true" />
			</a>
		</p>
	{/if}
</div>
