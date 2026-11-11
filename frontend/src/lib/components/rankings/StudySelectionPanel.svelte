<script lang="ts">
	/**
	 * "Study selection" on the rankings page: which models the study runs and why.
	 * Shows a stored selection snapshot (the confirmed one, else the newest draft;
	 * any older one from the list) or a live preview on the current data with
	 * N, tier quotas and budgets as parameters (writes nothing). Staff save the
	 * preview's parameters as a draft snapshot (full sensitivity draws, about a
	 * minute) and confirm a draft with swaps, reasons and notes. Confirmed
	 * snapshots are read-only and show who confirmed them and when.
	 */
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { NumberField } from '$lib/components/studies/shared';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import Save from '@lucide/svelte/icons/save';
	import BadgeCheck from '@lucide/svelte/icons/badge-check';
	import ListChecks from '@lucide/svelte/icons/list-checks';
	import { toast } from 'svelte-sonner';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getMe } from '$lib/api/users';
	import {
		createSelectionSnapshot,
		getLatestSelection,
		getSelectionPreview,
		getSelectionSnapshot,
		listSelectionSnapshots,
		type SelectionPayload,
		type SelectionSnapshotDetail,
		type SelectionSnapshotSummary,
	} from '$lib/api/rankings';
	import ConfirmSelectionDialog from './ConfirmSelectionDialog.svelte';
	import SelectionView from './SelectionView.svelte';
	import { candidateIndex, defaultQuotas, fmtDate, quotaProblem, shortName } from './mss';

	type Mode = 'snapshot' | 'preview';

	let isStaff = $state(false);
	let mode = $state<Mode>('snapshot');

	// Snapshots
	let snapshots = $state<SelectionSnapshotSummary[]>([]);
	let selectedId = $state('');
	let detail = $state<SelectionSnapshotDetail | null>(null);
	let snapLoading = $state(true);
	let snapError = $state('');

	// Preview parameters (A1 defaults: N = 10 → 3 frontier / 3 mid / 4 budget)
	let n = $state<number | null>(10);
	let frontier = $state<number | null>(3);
	let mid = $state<number | null>(3);
	let budget = $state<number | null>(4);
	let openrouterBudget = $state<number | null>(null);
	let anthropicBudget = $state<number | null>(null);
	let studyMonths = $state<number | null>(null);
	let includePreviews = $state(false);
	let withSensitivity = $state(false);

	let preview = $state<SelectionPayload | null>(null);
	let previewLoading = $state(false);
	let previewError = $state('');
	let previewCtl: AbortController | null = null;

	let creating = $state(false);
	let confirmOpen = $state(false);

	const quotaError = $derived(quotaProblem(n, { frontier, mid, budget }));

	function onN(v: number | null) {
		if (v == null) return;
		const q = defaultQuotas(v);
		frontier = q.frontier;
		mid = q.mid;
		budget = q.budget;
	}

	function previewParams() {
		return {
			n: n ?? undefined,
			frontier: frontier ?? undefined,
			mid: mid ?? undefined,
			budget: budget ?? undefined,
			include_previews: includePreviews || undefined,
			sensitivity: withSensitivity || undefined,
			openrouter_budget: openrouterBudget ?? undefined,
			anthropic_budget: anthropicBudget ?? undefined,
			study_months: studyMonths ?? undefined,
		};
	}

	async function runPreview() {
		if (quotaError) return;
		previewCtl?.abort();
		const ctl = new AbortController();
		previewCtl = ctl;
		previewLoading = true;
		previewError = '';
		try {
			preview = await getSelectionPreview(previewParams(), { signal: ctl.signal });
		} catch (e) {
			if (isAbortError(e)) return;
			previewError = formatApiError(e, 'Could not compute the selection preview.');
		} finally {
			if (previewCtl === ctl) {
				previewLoading = false;
				previewCtl = null;
			}
		}
	}

	async function loadSnapshots(select?: number) {
		snapLoading = true;
		snapError = '';
		try {
			const [latest, list] = await Promise.all([getLatestSelection(), listSelectionSnapshots()]);
			snapshots = list;
			const pick = select != null ? list.find((s) => s.id === select) : null;
			if (pick) {
				selectedId = String(pick.id);
				// The latest snapshot is already loaded (e.g. right after a confirm): no second download.
				detail = latest && latest.id === pick.id ? latest : await getSelectionSnapshot(pick.id);
			} else if (latest) {
				selectedId = String(latest.id);
				detail = latest;
			} else {
				selectedId = '';
				detail = null;
			}
		} catch (e) {
			snapError = formatApiError(e, 'Could not load the selection snapshots.');
		} finally {
			snapLoading = false;
		}
	}

	async function openSnapshot(id: string) {
		selectedId = id;
		if (!id) return;
		snapLoading = true;
		snapError = '';
		try {
			detail = await getSelectionSnapshot(Number(id));
		} catch (e) {
			snapError = formatApiError(e, 'Could not load the snapshot.');
		} finally {
			snapLoading = false;
		}
	}

	async function createSnapshot() {
		if (quotaError || creating) return;
		creating = true;
		try {
			const created = await createSelectionSnapshot({
				n: n ?? undefined,
				tier_quotas: { frontier: frontier ?? 0, mid: mid ?? 0, budget: budget ?? 0 },
				include_previews: includePreviews,
				openrouter_budget: openrouterBudget ?? undefined,
				anthropic_budget: anthropicBudget ?? undefined,
				study_months: studyMonths ?? undefined,
				sensitivity: true,
			});
			toast.success(`Draft snapshot #${created.id} saved`);
			mode = 'snapshot';
			snapshots = [created, ...snapshots.filter((s) => s.id !== created.id)];
			selectedId = String(created.id);
			detail = created;
		} catch (e) {
			toast.error(formatApiError(e, 'Could not create the snapshot.'));
		} finally {
			creating = false;
		}
	}

	function onConfirmed(d: SelectionSnapshotDetail) {
		toast.success(`Snapshot #${d.id} confirmed`);
		void loadSnapshots(d.id);
	}

	function setMode(v: string) {
		mode = v as Mode;
		if (mode === 'preview' && !preview && !previewLoading) void runPreview();
	}

	onMount(() => {
		getMe()
			.then((me) => (isStaff = me.is_staff ?? false))
			.catch(() => {});
		void loadSnapshots().then(() => {
			// Nothing stored yet: show what the current data would pick.
			if (!detail && !snapError) setMode('preview');
		});
		return () => previewCtl?.abort();
	});

	const snapshotItems = $derived(
		snapshots.map((s) => ({
			value: String(s.id),
			label: `#${s.id} · ${s.status} · ${fmtDate(s.as_of ?? s.created_at)}`,
			description: `${s.picks.length} picks · ${s.created_by ?? 'unknown'} · ${fmtDate(s.created_at)}`,
		})),
	);
	const shown = $derived(mode === 'snapshot' ? (detail?.payload ?? null) : preview);
	const conf = $derived(detail?.confirmation ?? {});
	const confCands = $derived(candidateIndex(detail?.payload));
	const statusTone = (s: string) => (s === 'confirmed' ? 'success' : s === 'draft' ? 'info' : 'neutral') as 'success' | 'info' | 'neutral';
</script>

<div class="space-y-4">
	<Card.Root class="gap-0 py-0">
		<div class="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
			<Segmented
				aria-label="Selection source"
				size="sm"
				value={mode}
				onValueChange={setMode}
				items={[
					{ value: 'snapshot', label: 'Snapshot', title: 'A stored, reproducible selection (inputs, parameters, code version)' },
					{ value: 'preview', label: 'Live preview', title: 'Computed now on the current data; nothing is written' },
				]}
			/>
			{#if mode === 'snapshot'}
				{#if snapshots.length}
					<Select.Simple
						size="sm"
						class="w-full sm:w-72"
						aria-label="Selection snapshot"
						value={selectedId}
						items={snapshotItems}
						onValueChange={openSnapshot}
					/>
				{/if}
				{#if detail}
					<StatusBadge status={detail.status} tone={statusTone(detail.status)} />
					<span class="text-[11px] text-muted-foreground" title="inputs sha256 {detail.inputs_sha256} · code {detail.code_version}">
						{detail.version} · created {fmtDate(detail.created_at)}{detail.created_by ? ` by ${detail.created_by}` : ''} · inputs {detail.inputs_sha256.slice(0, 10)}
					</span>
					{#if isStaff && detail.status === 'draft' && detail.payload.selection}
						<Button size="sm" class="ml-auto" onclick={() => (confirmOpen = true)}><BadgeCheck class="size-3.5" />Confirm…</Button>
					{/if}
				{/if}
			{:else}
				<span class="text-[11px] text-muted-foreground">Computed on the current data; nothing is written.</span>
			{/if}
		</div>

		{#if mode === 'preview'}
			<form
				class="grid gap-3 px-4 py-3 sm:grid-cols-3 lg:grid-cols-[repeat(7,minmax(0,1fr))_auto]"
				onsubmit={(e) => {
					e.preventDefault();
					void runPreview();
				}}
			>
				<NumberField id="sel-n" label="N models" bind:value={n} min={1} max={40} onchange={onN} />
				<NumberField id="sel-frontier" label="Frontier" bind:value={frontier} min={0} max={40} />
				<NumberField id="sel-mid" label="Mid" bind:value={mid} min={0} max={40} />
				<NumberField id="sel-budget" label="Budget" bind:value={budget} min={0} max={40} />
				<NumberField id="sel-or" label="OpenRouter $" bind:value={openrouterBudget} min={0} integer={false} placeholder="live credit" hint="before the 15% contingency" />
				<NumberField id="sel-anth" label="Anthropic $" bind:value={anthropicBudget} min={0} integer={false} placeholder="200" />
				<NumberField id="sel-months" label="Study months" bind:value={studyMonths} min={1} max={12} integer={false} placeholder="1" hint="Go monthly limits" />
				<div class="flex flex-col justify-end gap-2 sm:col-span-3 lg:col-span-1">
					<label class="flex items-center gap-2 text-xs"><Checkbox bind:checked={includePreviews} />Include previews</label>
					<label class="flex items-center gap-2 text-xs" title="Capped draws: S1 1,000, S2 500, S3 0"><Checkbox bind:checked={withSensitivity} />Sensitivity (capped)</label>
				</div>
				<div class="flex flex-wrap items-center gap-2 sm:col-span-3 lg:col-span-8">
					{#if quotaError}<span class="text-xs text-danger-fg" role="alert">{quotaError}</span>{/if}
					<span class="ml-auto"></span>
					{#if isStaff}
						<Button
							type="button"
							variant="outline"
							size="sm"
							disabled={!!quotaError || creating}
							title="Runs the full sensitivity draws (about a minute) and stores a reproducible draft"
							onclick={createSnapshot}
						>
							{#if creating}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />Saving (about a minute)…{:else}<Save class="size-3.5" />Save as draft snapshot{/if}
						</Button>
					{/if}
					<Button type="submit" size="sm" disabled={!!quotaError || previewLoading}>
						{#if previewLoading}<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />{:else}<Play class="size-3.5" />{/if}
						Run preview
					</Button>
				</div>
			</form>
		{/if}
	</Card.Root>

	{#if mode === 'snapshot' && detail && detail.status !== 'draft' && conf.final_ids}
		<Card.Root class="gap-0 py-0">
			<Card.Content class="space-y-2 p-4 text-xs">
				<p>
					<StatusBadge status={detail.status} tone={statusTone(detail.status)} />
					<span class="ml-2">Confirmed {detail.confirmed_at ? fmtDate(detail.confirmed_at) : ''}{detail.confirmed_by ? ` by ${detail.confirmed_by}` : ''}{detail.status === 'superseded' ? '; a later confirmation replaced it' : ''}. Read-only.</span>
				</p>
				<p><span class="text-muted-foreground">Final list:</span> {conf.final_ids.map((id) => shortName(id, confCands)).join(', ')}</p>
				{#if conf.overrides?.length}
					<ul class="space-y-0.5">
						{#each conf.overrides as o (o.replace)}
							<li><span class="text-muted-foreground">Swapped</span> {shortName(o.replace, confCands)} → {shortName(o.with, confCands)}: {o.reason}</li>
						{/each}
					</ul>
				{:else}
					<p class="text-muted-foreground">No swaps: the nominal picks.</p>
				{/if}
				{#if conf.notes}<p class="whitespace-pre-line"><span class="text-muted-foreground">Notes:</span> {conf.notes}</p>{/if}
			</Card.Content>
		</Card.Root>
	{/if}

	{#if mode === 'snapshot'}
		{#if snapError}
			<ErrorAlert message={snapError} onretry={() => loadSnapshots()} />
		{:else if snapLoading && !detail}
			<LoadingState label="loading the selection" />
		{:else if !detail}
			<EmptyState
				icon={ListChecks}
				title="No selection snapshot yet"
				description="Run a live preview, then save it as a draft snapshot (staff) or run manage.py select_models. A snapshot records the inputs, parameters, sensitivity and code version."
			>
				{#snippet action()}<Button size="sm" variant="outline" onclick={() => setMode('preview')}>Live preview</Button>{/snippet}
			</EmptyState>
		{/if}
	{:else if previewError}
		<ErrorAlert message={previewError} onretry={runPreview} />
	{:else if previewLoading && !preview}
		<LoadingState label={withSensitivity ? 'running the selection and capped sensitivity draws' : 'running the selection'} />
	{/if}

	{#if shown && !(mode === 'preview' && previewError) && !(mode === 'snapshot' && snapError)}
		<div class="transition-opacity duration-150 {(mode === 'preview' && previewLoading) || (mode === 'snapshot' && snapLoading) ? 'opacity-60' : ''}">
			<SelectionView payload={shown} />
		</div>
	{/if}
</div>

{#if detail && isStaff && detail.status === 'draft'}
	<ConfirmSelectionDialog bind:open={confirmOpen} snapshot={detail} onconfirmed={onConfirmed} />
{/if}
