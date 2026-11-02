<script lang="ts">
	/**
	 * AssignmentDetailSheet — one assignment, read-only, in a right-hand sheet:
	 * who and which apps (displayed sides), status, timing, quality flags,
	 * skip reason and hosting at skip time, the hosting provenance per slot
	 * (container, image, reset, prior users), every design block's answers
	 * (BlockAnswers), other observations, and the HTTP console log of this
	 * assignment. Exclude / include with a reason.
	 *
	 * Props: `task`, `assignmentId` (bindable; null closes the sheet),
	 * `onchanged(summary)` (exclusion changed).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Sheet from '$lib/components/ui/sheet';
	import { Button } from '$lib/components/ui/button';
	import { formatApiError } from '$lib/api/core';
	import {
		getAssignment,
		humanEvalErrorBody,
		listHttpLog,
		setAssignmentExclusion,
		type AssignmentDetail,
		type AssignmentSummary,
		type HttpExchangeRecord,
		type Observation,
		type TaskDetail,
	} from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { BLOCK_TYPE_LABELS, HOSTING_STATE_LABELS, PARTICIPANT_KIND_LABELS, formatSeconds, shortHash, skipReasonLabel } from '../format';
	import BlockAnswers from './BlockAnswers.svelte';
	import ExclusionDialog from './ExclusionDialog.svelte';
	import FlagList from './FlagList.svelte';
	import HttpExchangeList from './HttpExchangeList.svelte';
	import ObservationCard from './ObservationCard.svelte';
	import Ban from '@lucide/svelte/icons/ban';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import UserCheck from '@lucide/svelte/icons/user-check';

	interface Props {
		task: TaskDetail;
		assignmentId: string | null;
		onchanged?: (summary: AssignmentSummary) => void;
	}

	let { task, assignmentId = $bindable(null), onchanged }: Props = $props();

	let open = $state(false);
	let detail = $state.raw<AssignmentDetail | null>(null);
	let loading = $state(false);
	let loadError = $state('');
	/** Requests per page of the sheet's HTTP console list (the API's page cap). */
	const HTTP_SHEET_ROWS = 100;
	let http = $state.raw<HttpExchangeRecord[]>([]);
	let httpTotal = $state(0);
	let httpPage = $state(0);
	let httpPages = $state(0);
	let httpLoading = $state(false);
	let httpError = $state('');

	let seq = 0;
	async function load(id: string) {
		const mine = ++seq;
		loading = true;
		loadError = '';
		detail = null;
		http = [];
		httpTotal = 0;
		httpPage = 0;
		httpPages = 0;
		httpError = '';
		try {
			const d = await getAssignment(task.id, id);
			if (mine !== seq) return;
			detail = d;
			if (d.http_request_count > 0) void loadHttp(d, mine);
		} catch (e) {
			if (mine !== seq) return;
			loadError = (e as { httpStatus?: number })?.httpStatus === 404 ? 'This assignment no longer exists.' : formatApiError(e, 'Could not load the assignment.');
		} finally {
			if (mine === seq) loading = false;
		}
	}

	/** This assignment's requests, newest first, one page at a time (filtered by the server). */
	async function loadHttp(d: AssignmentDetail, mine: number, pageNo = 1) {
		httpLoading = true;
		httpError = '';
		try {
			const res = await listHttpLog(task.id, { assignment_id: d.id, page: pageNo, per_page: HTTP_SHEET_ROWS });
			if (mine !== seq) return;
			// Requests logged meanwhile shift the pages: skip rows already shown.
			const seen = new Set(pageNo === 1 ? [] : http.map((x) => x.id));
			http = [...(pageNo === 1 ? [] : http), ...res.items.filter((x) => !seen.has(x.id))];
			httpTotal = res.total;
			httpPage = pageNo;
			httpPages = res.pages;
		} catch (e) {
			if (mine === seq) httpError = formatApiError(e, 'Could not load the requests.');
		} finally {
			if (mine === seq) httpLoading = false;
		}
	}

	$effect(() => {
		const id = assignmentId;
		untrack(() => {
			open = !!id;
			if (id) void load(id);
		});
	});

	function onOpenChange(next: boolean) {
		if (!next) assignmentId = null;
	}

	const apps = $derived(detail?.apps ?? []);
	const blocks = $derived(task.design.blocks);
	const blockIds = $derived(new Set(blocks.map((b) => b.id)));
	const hostingSlots = $derived(Object.entries(detail?.context?.hosting ?? {}));
	const skipHosting = $derived(Object.entries(detail?.context?.skip_hosting ?? {}));

	/**
	 * Observations under the block that collected them: vulnerability reports
	 * under their security block, bug reports under their bugs block (reports
	 * without a known block go to the first block of that kind); the rest
	 * (notes, unknown blocks) are listed after the blocks.
	 */
	const observationGroups = $derived.by(() => {
		const all = detail?.observations ?? [];
		const byBlock = new Map<string, Observation[]>();
		const placed = new Set<string>();
		const firstOf = (type: string) => blocks.find((b) => b.type === type)?.id;
		for (const b of blocks) {
			const kind = b.type === 'security' ? 'vulnerability' : b.type === 'bugs' ? 'bug' : null;
			if (!kind) continue;
			const list = all.filter(
				(o) => o.kind === kind && (o.block_id === b.id || (!blockIds.has(o.block_id) && firstOf(b.type) === b.id))
			);
			for (const o of list) placed.add(o.id);
			byBlock.set(b.id, list);
		}
		return { byBlock, loose: all.filter((o) => !placed.has(o.id)) };
	});
	const looseObservations = $derived(observationGroups.loose);

	// ── exclusion ───────────────────────────────────────────────────
	let exclusionOpen = $state(false);
	async function submitExclusion(excluded: boolean, reason: string) {
		if (!detail) return;
		try {
			const summary = await setAssignmentExclusion(task.id, detail.id, { excluded, exclusion_reason: reason });
			detail = { ...detail, ...summary };
			onchanged?.(summary);
			toast.success(excluded ? 'Assignment excluded from analysis' : 'Assignment included again');
		} catch (e) {
			const body = humanEvalErrorBody(e);
			throw new Error(body?.detail || formatApiError(e, 'Could not update the assignment.'));
		}
	}
</script>

<Sheet.Root bind:open {onOpenChange}>
	<Sheet.Content side="right" class="w-full max-w-none gap-0 overflow-y-auto p-0 sm:max-w-3xl">
		<Sheet.Header class="border-b border-border px-5 py-4 pr-12">
			<Sheet.Title class="font-display">
				{#if detail}
					{detail.participant.label} · {detail.apps.map((a) => (a.slot === 'single' ? a.label : `${a.slot}: ${a.label}`)).join(' vs ')}
				{:else}
					Assignment
				{/if}
			</Sheet.Title>
			<Sheet.Description>Everything this participant saved for this item, as they saw it.</Sheet.Description>
		</Sheet.Header>

		<div class="space-y-6 px-5 py-4">
			{#if loading}
				<LoadingState label="loading assignment" />
			{:else if loadError}
				<ErrorAlert message={loadError} onretry={() => assignmentId && load(assignmentId)} />
			{:else if detail}
				<section class="space-y-3">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<div class="flex flex-wrap items-center gap-2">
							<StatusBadge status={detail.status} />
							{#if detail.excluded}<span class="text-xs font-medium text-danger-fg">excluded: {detail.exclusion_reason || 'no reason given'}</span>{/if}
						</div>
						{#if detail.participant.kind !== 'preview'}
							<Button variant="outline" size="sm" onclick={() => (exclusionOpen = true)}>
								{#if detail.excluded}<UserCheck />Include{:else}<Ban />Exclude{/if}
							</Button>
						{/if}
					</div>
					<dl class="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Participant</dt><dd>{detail.participant.label} · {PARTICIPANT_KIND_LABELS[detail.participant.kind] ?? detail.participant.kind}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Position</dt><dd class="font-mono tabular-nums">#{detail.position}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Started</dt><dd class="font-mono text-xs tabular-nums">{formatDateTime(detail.started_at)}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Submitted</dt><dd class="font-mono text-xs tabular-nums">{formatDateTime(detail.submitted_at)}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground" title="Visible-tab time">Active time</dt><dd class="font-mono tabular-nums">{formatSeconds(detail.active_seconds)}</dd></div>
						<div class="flex justify-between gap-2"><dt class="text-muted-foreground" title="Workbench open (visible or not), long gaps removed">Wall time</dt><dd class="font-mono tabular-nums">{formatSeconds(detail.wall_seconds)}</dd></div>
						{#if task.mode === 'pairwise'}
							<div class="flex justify-between gap-2 sm:col-span-2">
								<dt class="text-muted-foreground">Sides</dt>
								<dd class="text-right text-xs">{detail.apps.map((a) => `${a.slot} = ${a.label}`).join(', ')}{detail.swapped ? ' (swapped from canonical order)' : ''}</dd>
							</div>
						{/if}
						{#if detail.context?.instructions_sha256}
							<div class="flex justify-between gap-2"><dt class="text-muted-foreground" title="sha256 of the instructions this participant saw">Instructions</dt><dd class="font-mono text-xs" title={detail.context.instructions_sha256}>{shortHash(detail.context.instructions_sha256)}</dd></div>
						{/if}
					</dl>
					<div class="flex flex-wrap items-center gap-2 text-xs"><span class="text-muted-foreground">Quality flags</span><FlagList flags={detail.quality_flags} /></div>
					{#if detail.status === 'skipped'}
						<div class="rounded-md border border-border bg-muted/30 p-3 text-sm">
							<p><span class="font-medium">Skipped:</span> {skipReasonLabel(detail.skip_reason) || 'no reason'}</p>
							{#if detail.skip_note}<p class="mt-1 whitespace-pre-wrap break-words text-muted-foreground">{detail.skip_note}</p>{/if}
							{#if skipHosting.length > 0}
								<p class="mt-1 text-xs text-muted-foreground">
									Hosting at skip time:
									{#each skipHosting as [slot, h] (slot)}
										<span class="mr-2">{slot === 'single' ? '' : `${slot}: `}{HOSTING_STATE_LABELS[h.state] ?? h.state}{h.error ? ` — ${h.error}` : ''}</span>
									{/each}
								</p>
							{/if}
						</div>
					{/if}
					{#if hostingSlots.length > 0}
						<div class="space-y-1">
							<p class="section-label">App provenance</p>
							<div class="overflow-x-auto rounded-md border border-border">
								<table class="text-xs">
									<thead><tr><th>Slot</th><th>Container</th><th>Image</th><th title="Earlier assignments that used this container's state">Prior users</th><th>Reset</th><th>First running</th></tr></thead>
									<tbody>
										{#each hostingSlots as [slot, h] (slot)}
											<tr>
												<td>{slot}</td>
												<td class="font-mono">{h.container_name ?? '—'}</td>
												<td class="max-w-48 truncate font-mono" title={h.image}>{h.image || '—'}</td>
												<td class="num">{h.prior_users}</td>
												<td class="font-mono">{h.reset_at ? formatDateTime(h.reset_at) : '—'}</td>
												<td class="font-mono">{h.first_running_at ? formatDateTime(h.first_running_at) : '—'}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						</div>
					{/if}
				</section>

				{#each blocks as block, i (block.id)}
					{@const response = detail.responses[block.id]}
					<section class="space-y-2">
						<SectionHeading size="sm" number={i + 1} title={block.title || BLOCK_TYPE_LABELS[block.type]} count={BLOCK_TYPE_LABELS[block.type]} />
						<BlockAnswers
							{block}
							answers={response?.answers}
							updatedAt={response?.updated_at ?? null}
							context={detail.context?.blocks?.[block.id]}
							{apps}
							observations={observationGroups.byBlock.get(block.id) ?? []}
							verdicts={detail.finding_verdicts}
						/>
					</section>
				{/each}

				{#if looseObservations.length > 0}
					<section class="space-y-2">
						<SectionHeading size="sm" title="Other observations" count={looseObservations.length} />
						{#each looseObservations as o (o.id)}
							<ObservationCard observation={o} appLabel={apps.find((a) => a.slot === o.app_slot)?.label ?? ''} />
						{/each}
					</section>
				{/if}

				{#if detail.http_request_count > 0}
					<section class="space-y-2">
						<SectionHeading size="sm" title="HTTP console" count={detail.http_request_count} />
						{#if httpLoading && http.length === 0}
							<p class="text-sm text-muted-foreground">Loading requests…</p>
						{:else if httpError && http.length === 0}
							<ErrorAlert message={httpError} onretry={() => detail && loadHttp(detail, seq)} />
						{:else if http.length === 0}
							<p class="text-sm text-muted-foreground">No requests are logged for this assignment.</p>
						{:else}
							<HttpExchangeList exchanges={http} />
							{#if httpError}
								<p class="text-xs text-danger-fg">{httpError}</p>
							{/if}
							{#if httpPage < httpPages}
								<div class="flex flex-wrap items-center gap-2">
									<p class="text-xs text-muted-foreground">Showing {http.length} of {httpTotal}</p>
									<Button
										variant="outline"
										size="sm"
										disabled={httpLoading}
										aria-busy={httpLoading}
										onclick={() => detail && loadHttp(detail, seq, httpPage + 1)}
									>
										{#if httpLoading}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Show {Math.min(HTTP_SHEET_ROWS, httpTotal - http.length)} more
									</Button>
								</div>
							{/if}
						{/if}
					</section>
				{/if}
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>

{#if detail}
	<ExclusionDialog
		bind:open={exclusionOpen}
		subject="this assignment"
		excluded={detail.excluded}
		reason={detail.exclusion_reason}
		postHoc={!!task.opened_at}
		onsubmit={submitExclusion}
	/>
{/if}
