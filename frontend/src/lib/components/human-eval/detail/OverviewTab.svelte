<script lang="ts">
	/**
	 * OverviewTab — the task at a glance: why it can't open yet (open_blockers),
	 * validity warnings (open_warnings) and environment warnings (apps not
	 * reachable, shared client IP), the KPI row, data-collection progress
	 * (assignments by status, ratings-per-item coverage, submissions per day),
	 * the record (timeline, seed, design hash, owner), the protocol log of
	 * post-open edits and the preview reset.
	 *
	 * Props: `task` (TaskDetail), `environment` (HumanEvalEnvironment | null).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import KpiCard from '$lib/components/dashboard/KpiCard.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { formatApiError } from '$lib/api/core';
	import { getProgress, resetPreview, type HumanEvalEnvironment, type TaskDetail, type TaskProgress } from '$lib/api/human-eval';
	import { statusTone, toneFillClasses } from '$lib/constants/colors';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import {
		ENVIRONMENT_WARNING_TEXT,
		OPEN_WARNING_TEXT,
		TASK_STATUS_HELP,
		codeText,
		formatShare,
		plural,
		shortHash,
	} from '../format';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import ClipboardCheck from '@lucide/svelte/icons/clipboard-check';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Users from '@lucide/svelte/icons/users';

	interface Props {
		task: TaskDetail;
		environment: HumanEvalEnvironment | null;
	}

	let { task, environment }: Props = $props();

	const counts = $derived(task.counts);
	/** The left column (progress, protocol log) has something to show. */
	const hasMain = $derived(task.status !== 'draft' || task.protocol_log.length > 0);
	const isDraft = $derived(task.status === 'draft');

	let progress = $state.raw<TaskProgress | null>(null);
	let progressError = $state('');
	let progressLoading = $state(false);
	let ctl: AbortController | null = null;

	async function loadProgress() {
		ctl?.abort();
		const mine = new AbortController();
		ctl = mine;
		progressLoading = true;
		try {
			progress = await getProgress(task.id, { signal: mine.signal });
			progressError = '';
		} catch (e) {
			if (mine.signal.aborted) return;
			progressError = formatApiError(e, 'Could not load progress.');
		} finally {
			if (ctl === mine) progressLoading = false;
		}
	}

	$effect(() => {
		const id = task.id;
		const draft = isDraft;
		void counts.assignments_submitted;
		untrack(() => {
			if (!draft && id) void loadProgress();
		});
		return () => ctl?.abort();
	});

	const ASSIGNMENT_ORDER = ['submitted', 'in_progress', 'assigned', 'skipped', 'expired'] as const;
	const assignmentTotal = $derived(
		progress ? ASSIGNMENT_ORDER.reduce((sum, s) => sum + (progress?.assignments_by_status[s] ?? 0), 0) : 0
	);
	const coverageMax = $derived(Math.max(1, ...(progress?.coverage_histogram ?? []).map((b) => b.items)));
	const timeline = $derived((progress?.timeline ?? []).slice(-30));
	const timelineMax = $derived(Math.max(1, ...timeline.map((d) => d.submitted)));
	const targetShare = $derived(
		counts.target_ratings ? Math.min(1, counts.assignments_submitted / counts.target_ratings) : null
	);

	const envWarnings = $derived.by(() => {
		if (!environment) return [] as string[];
		const list = [...environment.warnings];
		if (!environment.apps_reachable && !list.includes('apps_not_reachable')) list.unshift('apps_not_reachable');
		return list;
	});

	const timelineRows = $derived(
		[
			{ label: 'Created', at: task.created_at },
			{ label: 'Opens at (scheduled)', at: task.opens_at },
			{ label: 'Design frozen', at: task.frozen_at },
			{ label: 'First opened', at: task.opened_at },
			{ label: 'First closed', at: task.first_closed_at },
			{ label: 'Closed', at: task.closed_at },
			{ label: 'Closes at (scheduled)', at: task.closes_at },
		].filter((r) => r.at)
	);

	let resetOpen = $state(false);
	let resetting = $state(false);
	async function doResetPreview() {
		resetting = true;
		try {
			await resetPreview(task.id);
			toast.success('Preview data deleted');
			resetOpen = false;
		} catch (e) {
			toast.error(formatApiError(e, 'Could not reset the preview.'));
		} finally {
			resetting = false;
		}
	}

	async function copyHash() {
		try {
			await navigator.clipboard.writeText(task.design_hash);
			toast.success('Design hash copied');
		} catch {
			toast.error('Could not copy.');
		}
	}
</script>

<div class="space-y-4">
	{#if isDraft && task.open_blockers.length > 0}
		<Alert>
			<CircleAlert />
			<div class="space-y-1">
				<p class="font-medium">Before this task can open</p>
				<ul class="list-disc space-y-0.5 pl-4 text-sm text-muted-foreground">
					{#each task.open_blockers as blocker (blocker)}<li>{codeText(blocker)}</li>{/each}
				</ul>
			</div>
		</Alert>
	{:else if isDraft}
		<Alert>
			<ClipboardCheck />
			<AlertDescription>
				Ready to open. Opening freezes the design and measurement settings, shuffles the blinded labels and builds the
				items. Set up participant access first (Participants tab) — codes, invites and accounts can be prepared now.
			</AlertDescription>
		</Alert>
	{/if}

	{#if task.open_warnings.length > 0}
		<Alert class="border-warning/40 bg-warning/5">
			<TriangleAlert class="text-warning-fg" />
			<div class="space-y-1">
				<p class="font-medium">Validity warnings</p>
				<ul class="list-disc space-y-0.5 pl-4 text-sm text-muted-foreground">
					{#each task.open_warnings as warning (warning)}<li>{codeText(warning, OPEN_WARNING_TEXT)}</li>{/each}
				</ul>
			</div>
		</Alert>
	{/if}

	{#if envWarnings.length > 0}
		<Alert class="border-warning/40 bg-warning/5">
			<TriangleAlert class="text-warning-fg" />
			<div class="space-y-1">
				<p class="font-medium">Environment</p>
				<ul class="list-disc space-y-0.5 pl-4 text-sm text-muted-foreground">
					{#each envWarnings as warning (warning)}<li>{codeText(warning, ENVIRONMENT_WARNING_TEXT)}</li>{/each}
				</ul>
			</div>
		</Alert>
	{/if}

	<section class="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Task counts">
		<KpiCard
			title="Ratings"
			value={counts.assignments_submitted}
			subtitle={counts.target_ratings != null ? `of ${counts.target_ratings.toLocaleString()} targeted` : 'target set at open'}
			icon={ClipboardCheck}
			href="?tab=responses"
		/>
		<KpiCard
			title="Participants"
			value={counts.participants}
			subtitle={`${counts.participants_active} active · ${counts.participants_completed} completed`}
			icon={Users}
			href="?tab=participants"
			accent="cyan"
		/>
		<KpiCard
			title="Apps"
			value={counts.active_apps}
			subtitle={`${counts.apps} in pool · ${plural(counts.items, 'item')}`}
			icon={AppWindow}
			href="?tab=apps"
			accent="violet"
		/>
		<KpiCard
			title="In progress"
			value={counts.assignments_in_progress}
			subtitle={`${counts.assignments_skipped} skipped`}
			icon={Hourglass}
			href="?tab=responses"
			accent="amber"
		/>
	</section>

	<div class={cn('grid gap-4', hasMain && 'xl:grid-cols-[minmax(0,1fr)_22rem]')}>
		{#if hasMain}
			<div class="min-w-0 space-y-4">
				{#if !isDraft}
					<Card.Root class="gap-0 py-0">
						<div class="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
							<Card.Title>Data collection</Card.Title>
							<Button variant="ghost" size="sm" onclick={loadProgress} disabled={progressLoading} aria-label="Refresh progress">
								<RefreshCw class={cn(progressLoading && 'animate-spin motion-reduce:animate-none')} />
							</Button>
						</div>
						<div class="space-y-5 p-4">
							{#if progressError}
								<ErrorAlert message={progressError} onretry={loadProgress} />
							{/if}

							<div class="space-y-1.5">
								<div class="flex items-baseline justify-between gap-2 text-sm">
									<span class="section-label">Toward the target</span>
									<span class="font-mono text-xs tabular-nums text-muted-foreground">
										{counts.assignments_submitted.toLocaleString()}{counts.target_ratings != null ? ` / ${counts.target_ratings.toLocaleString()}` : ''}
										{#if targetShare != null}· {formatShare(targetShare)}{/if}
									</span>
								</div>
								<div
									class="h-2 overflow-hidden rounded-full bg-muted"
									role="meter"
									aria-label="Submitted ratings toward the target"
									aria-valuemin={0}
									aria-valuemax={counts.target_ratings ?? 0}
									aria-valuenow={counts.assignments_submitted}
								>
									<div class="h-full bg-chart-1" style="width: {(targetShare ?? 0) * 100}%"></div>
								</div>
								<p class="text-xs text-muted-foreground">
									{task.settings.allocation.strategy === 'balanced'
										? `Balanced allocation: ${task.settings.allocation.ratings_per_item} ratings per item.`
										: 'Every participant rates every item.'}
									{#if progress?.median_minutes_per_item != null}
										Median {progress.median_minutes_per_item.toFixed(1)} min per item.
									{/if}
								</p>
							</div>

							{#if progress}
								<div class="space-y-1.5">
									<span class="section-label">Assignments by status</span>
									{#if assignmentTotal === 0}
										<p class="text-sm text-muted-foreground">No assignments yet.</p>
									{:else}
										<div class="flex h-2.5 overflow-hidden rounded-full bg-muted" aria-hidden="true">
											{#each ASSIGNMENT_ORDER as status (status)}
												{@const n = progress.assignments_by_status[status] ?? 0}
												{#if n > 0}
													<div class={toneFillClasses[statusTone(status)]} style="width: {(n / assignmentTotal) * 100}%"></div>
												{/if}
											{/each}
										</div>
										<ul class="flex flex-wrap gap-x-4 gap-y-1 text-xs">
											{#each ASSIGNMENT_ORDER as status (status)}
												<li class="inline-flex items-center gap-1.5">
													<span class={cn('size-2 rounded-sm', toneFillClasses[statusTone(status)])} aria-hidden="true"></span>
													<span class="capitalize text-muted-foreground">{status.replace('_', ' ')}</span>
													<span class="font-mono tabular-nums">{progress.assignments_by_status[status] ?? 0}</span>
												</li>
											{/each}
										</ul>
									{/if}
								</div>

								<div class="grid gap-5 md:grid-cols-2">
									<div class="space-y-1.5">
										<span class="section-label">Ratings per item</span>
										{#if progress.coverage_histogram.length === 0}
											<p class="text-sm text-muted-foreground">No items yet.</p>
										{:else}
											<ul class="space-y-1" aria-label="Number of items by submitted ratings">
												{#each progress.coverage_histogram as bin (bin.ratings)}
													<li class="grid grid-cols-[4.5rem_1fr_2.5rem] items-center gap-2 text-xs">
														<span class="font-mono tabular-nums text-muted-foreground">{plural(bin.ratings, 'rating')}</span>
														<span class="h-2 rounded-sm bg-muted"><span class="block h-full rounded-sm bg-chart-1" style="width: {(bin.items / coverageMax) * 100}%"></span></span>
														<span class="text-right font-mono tabular-nums">{bin.items}</span>
													</li>
												{/each}
											</ul>
											<p class="text-[11px] text-muted-foreground">Items (right) by how many submitted ratings they have.</p>
										{/if}
									</div>
									<div class="space-y-1.5">
										<span class="section-label">Submissions per day (UTC)</span>
										{#if timeline.length === 0}
											<p class="text-sm text-muted-foreground">Nothing submitted yet.</p>
										{:else}
											<div class="flex h-24 items-end gap-0.5" role="img" aria-label="Submissions per day: {timeline.map((d) => `${d.date} ${d.submitted}`).join(', ')}">
												{#each timeline as day (day.date)}
													<span
														class="min-w-1 flex-1 rounded-t-sm bg-chart-1"
														style="height: {Math.max(2, (day.submitted / timelineMax) * 100)}%"
														title="{day.date}: {day.submitted} submitted"
													></span>
												{/each}
											</div>
											<div class="flex justify-between font-mono text-[11px] text-muted-foreground">
												<span>{timeline[0].date}</span>
												<span>max {timelineMax}/day</span>
												<span>{timeline[timeline.length - 1].date}</span>
											</div>
										{/if}
									</div>
								</div>
							{/if}
						</div>
					</Card.Root>
				{/if}

				{#if task.protocol_log.length > 0}
					<Card.Root class="gap-0 overflow-clip py-0">
						<Card.Header class="border-b border-border py-3">
							<Card.Title>Protocol log</Card.Title>
							<Card.Description>Edits to what participants read after the task opened (hashes of the old and new text).</Card.Description>
						</Card.Header>
						<div class="overflow-x-auto">
							<table>
								<thead>
									<tr><th>When</th><th>By</th><th>Field</th><th>Old</th><th>New</th></tr>
								</thead>
								<tbody>
									{#each task.protocol_log as entry, i (i)}
										<tr>
											<td class="whitespace-nowrap text-xs">{formatDateTime(entry.at)}</td>
											<td class="text-xs">{entry.by}</td>
											<td class="font-mono text-xs">{entry.field}</td>
											<td class="font-mono text-xs text-muted-foreground" title={entry.old_sha256}>{shortHash(entry.old_sha256)}</td>
											<td class="font-mono text-xs" title={entry.new_sha256}>{shortHash(entry.new_sha256)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					</Card.Root>
				{/if}
			</div>
		{/if}

		<Card.Root class={cn('h-fit gap-0 py-0', !hasMain && 'max-w-xl')}>
			<div class="border-b border-border px-4 py-2.5"><Card.Title>Record</Card.Title></div>
			<dl class="divide-y divide-border/60 text-sm">
				<div class="flex items-center justify-between gap-3 px-4 py-2">
					<dt class="text-muted-foreground">Status</dt>
					<dd class="text-right"><StatusBadge status={task.status} /><span class="sr-only"> — {TASK_STATUS_HELP[task.status]}</span></dd>
				</div>
				{#each timelineRows as row (row.label)}
					<div class="flex items-center justify-between gap-3 px-4 py-2">
						<dt class="text-muted-foreground">{row.label}</dt>
						<dd class="text-right font-mono text-xs tabular-nums">{formatDateTime(row.at)}</dd>
					</div>
				{/each}
				<div class="flex items-center justify-between gap-3 px-4 py-2">
					<dt class="text-muted-foreground">Seed</dt>
					<dd class="font-mono text-xs tabular-nums">{task.seed}</dd>
				</div>
				<div class="flex items-center justify-between gap-3 px-4 py-2">
					<dt class="text-muted-foreground" title="sha256 of the frozen design and measurement settings">Design hash</dt>
					<dd>
						{#if task.design_hash}
							<button type="button" class="cursor-pointer font-mono text-xs hover:text-primary" title="{task.design_hash} — click to copy" onclick={copyHash}>
								{shortHash(task.design_hash, 12)}
							</button>
						{:else}
							<span class="text-xs text-muted-foreground">set at open</span>
						{/if}
					</dd>
				</div>
				{#if task.experiment}
					<div class="flex items-center justify-between gap-3 px-4 py-2">
						<dt class="text-muted-foreground">Experiment</dt>
						<dd class="min-w-0 truncate text-right"><a class="settings-link" href="/experiments/{task.experiment.id}">{task.experiment.name}</a></dd>
					</div>
				{/if}
				{#if task.owner}
					<div class="flex items-center justify-between gap-3 px-4 py-2">
						<dt class="text-muted-foreground">Owner</dt>
						<dd class="min-w-0 truncate text-right text-xs">{task.owner.name || task.owner.email}</dd>
					</div>
				{/if}
				<div class="flex items-center justify-between gap-3 px-4 py-2">
					<dt class="text-muted-foreground">Access</dt>
					<dd class="text-right text-xs">
						{[
							task.settings.access.allow_invite_links && 'invite links',
							task.settings.access.allow_access_codes && 'access codes',
							task.settings.access.allow_accounts && 'accounts',
						]
							.filter(Boolean)
							.join(' · ') || 'none enabled'}
					</dd>
				</div>
			</dl>
			{#if !isDraft}
				<div class="settings-footer">
					<span class="text-xs text-muted-foreground">Your preview participant never enters results or exports.</span>
					<Button variant="outline" size="sm" onclick={() => (resetOpen = true)}>Reset preview</Button>
				</div>
			{/if}
		</Card.Root>
	</div>
</div>

<ConfirmDialog
	bind:open={resetOpen}
	title="Reset your preview?"
	description="Deletes your preview participant and everything it saved in this task (answers, observations, screenshots). The next preview starts fresh."
	confirmLabel="Reset preview"
	destructive
	busy={resetting}
	onConfirm={doResetPreview}
/>
