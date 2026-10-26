<script lang="ts">
	/**
	 * TargetPicker — choose which generation jobs a study analyses and see
	 * them resolved live (eligibility, sample size) before saving.
	 *
	 * Sources: an experiment (optionally some conditions / apps), a batch,
	 * hand-picked applications (jobs) or a filter over every completed job.
	 *
	 * Props:
	 * - `spec` (bindable `TargetSpec | null`; null while incomplete);
	 * - `options` (`GET /targets/options/`);
	 * - `exclusionRules`, `profileId`: resolve under the study's rules/scope;
	 * - `errors`: `target_spec.*` field errors from the server;
	 * - `resolved` (bindable output): the latest resolution (page 1 counts);
	 * - `disabled`; `onchange(spec)` after each user edit.
	 * The initial `spec` is read once; re-mount the picker to reset it.
	 */
	import { untrack } from 'svelte';
	import {
		resolveTargets,
		type ExclusionRules,
		type TargetFilters,
		type TargetOptions,
		type TargetResolveResult,
		type TargetSpec,
		type TargetSpecKind
	} from '$lib/api/analysis';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import * as Select from '$lib/components/ui/select';
	import * as Card from '$lib/components/ui/card';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import Layers from '@lucide/svelte/icons/layers';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Filter from '@lucide/svelte/icons/filter';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Info from '@lucide/svelte/icons/info';
	import CheckList from './CheckList.svelte';
	import JobPicker from './JobPicker.svelte';
	import TargetTable from './TargetTable.svelte';
	import { exclusionLabel } from '$lib/components/insights/format';

	interface Props {
		spec?: TargetSpec | null;
		options: TargetOptions | null;
		exclusionRules?: Partial<ExclusionRules> | null;
		profileId?: string | null;
		errors?: Record<string, string>;
		resolved?: TargetResolveResult | null;
		disabled?: boolean;
		onchange?: (spec: TargetSpec | null) => void;
	}

	let {
		spec = $bindable(null),
		options,
		exclusionRules = null,
		profileId = null,
		errors = {},
		resolved = $bindable(null),
		disabled = false,
		onchange
	}: Props = $props();

	// ── Local selection state, seeded once from the incoming spec ──────────
	const initial = untrack(() => spec);
	const initialFilters: TargetFilters = initial?.filters ?? {};

	let source = $state<TargetSpecKind>(initial?.kind ?? 'experiment');
	let experimentId = $state(initial?.kind === 'experiment' ? initial.experiment_id : '');
	let conditionIds = $state<string[]>(
		initial?.kind === 'experiment' ? (initial.condition_ids ?? []).map(String) : []
	);
	let experimentApps = $state<string[]>(
		initial?.kind === 'experiment' ? (initial.app_slugs ?? []) : []
	);
	let batchId = $state(initial?.kind === 'batch' ? initial.batch_id : '');
	let jobIds = $state<string[]>(initial?.kind === 'jobs' ? initial.job_ids : []);
	let fModels = $state<string[]>(
		initial?.kind === 'filter' ? (initialFilters.model_ids ?? []).map(String) : []
	);
	let fModes = $state<string[]>(initial?.kind === 'filter' ? (initialFilters.modes ?? []) : []);
	let fHarnesses = $state<string[]>(
		initial?.kind === 'filter' ? (initialFilters.harnesses ?? []) : []
	);
	let fStacks = $state<string[]>(initial?.kind === 'filter' ? (initialFilters.stacks ?? []) : []);
	let fApps = $state<string[]>(initial?.kind === 'filter' ? (initialFilters.app_slugs ?? []) : []);
	let fAfter = $state(
		initial?.kind === 'filter' ? (initialFilters.created_after ?? '').slice(0, 10) : ''
	);
	let fBefore = $state(
		initial?.kind === 'filter' ? (initialFilters.created_before ?? '').slice(0, 10) : ''
	);

	const experiment = $derived(options?.experiments.find((e) => e.id === experimentId) ?? null);

	const SOURCES = [
		{ value: 'experiment', label: 'Experiment', icon: FlaskConical },
		{ value: 'batch', label: 'Batch', icon: Layers },
		{ value: 'jobs', label: 'Applications', icon: AppWindow },
		{ value: 'filter', label: 'Filter', icon: Filter }
	];

	function build(): TargetSpec | null {
		switch (source) {
			case 'experiment': {
				if (!experimentId) return null;
				const out: TargetSpec = {
					kind: 'experiment',
					experiment_id: experimentId
				};
				const allConditions = experiment?.conditions.length ?? 0;
				if (conditionIds.length > 0 && conditionIds.length < allConditions) {
					out.condition_ids = conditionIds.map(Number);
				}
				const allApps = experiment?.app_slugs.length ?? 0;
				if (experimentApps.length > 0 && experimentApps.length < allApps)
					out.app_slugs = experimentApps;
				return out;
			}
			case 'batch':
				return batchId ? { kind: 'batch', batch_id: batchId } : null;
			case 'jobs':
				return jobIds.length > 0 ? { kind: 'jobs', job_ids: jobIds } : null;
			case 'filter': {
				const filters: TargetFilters = {};
				if (fModels.length) filters.model_ids = fModels;
				if (fModes.length) filters.modes = fModes;
				if (fHarnesses.length) filters.harnesses = fHarnesses;
				if (fStacks.length) filters.stacks = fStacks;
				if (fApps.length) filters.app_slugs = fApps;
				if (fAfter) filters.created_after = fAfter;
				if (fBefore) filters.created_before = fBefore;
				return Object.keys(filters).length ? { kind: 'filter', filters } : { kind: 'filter' };
			}
		}
	}

	/** Push the current selection out (user edits only). */
	function emit() {
		const next = build();
		spec = next;
		onchange?.(next);
		page = 1;
	}

	function pickSource(value: string) {
		source = value as TargetSpecKind;
		emit();
	}

	function pickExperiment(id: string) {
		experimentId = id;
		// A new experiment starts with every condition and app.
		conditionIds = [];
		experimentApps = [];
		emit();
	}

	// ── Live resolution (debounced, superseded requests aborted) ───────────
	let page = $state(1);
	let resolving = $state(false);
	let resolveError = $state('');
	let resolveFieldErrors = $state<Record<string, string>>({});

	// Deep key: the rules object may be edited in place by the parent.
	const resolveKey = $derived(JSON.stringify({ spec, exclusionRules, profileId, page }));

	$effect(() => {
		const key = resolveKey;
		const request = untrack(() => ({
			spec: $state.snapshot(spec) as TargetSpec | null,
			rules: exclusionRules
				? ($state.snapshot(exclusionRules) as Partial<ExclusionRules>)
				: undefined,
			profileId: profileId ?? undefined,
			page
		}));
		if (!request.spec) {
			untrack(() => {
				resolved = null;
				resolveError = '';
				resolveFieldErrors = {};
			});
			return;
		}
		const target = request.spec;
		const ctrl = new AbortController();
		const timer = setTimeout(async () => {
			resolving = true;
			resolveError = '';
			try {
				const res = await resolveTargets(
					{
						target_spec: target,
						exclusion_rules: request.rules,
						profile_id: request.profileId,
						page: request.page,
						per_page: 25
					},
					{ signal: ctrl.signal }
				);
				if (ctrl.signal.aborted) return;
				resolved = res;
				resolveFieldErrors = {};
			} catch (e) {
				if (isAbortError(e) || ctrl.signal.aborted) return;
				resolveFieldErrors = apiFieldErrors(e);
				resolveError = formatApiError(e, 'Could not resolve the targets.');
				resolved = null;
			} finally {
				if (!ctrl.signal.aborted) resolving = false;
			}
		}, 350);
		return () => {
			void key;
			clearTimeout(timer);
			ctrl.abort();
		};
	});

	const fieldErrors = $derived({ ...resolveFieldErrors, ...errors });
	const specErrors = $derived(
		Object.entries(fieldErrors)
			.filter(([k]) => k.startsWith('target_spec'))
			.map(([k, v]) => `${k.replace(/^target_spec\.?/, '') || 'targets'}: ${v}`)
	);

	const excludedOnPage = $derived(resolved ? resolved.items.filter((t) => !t.eligible).length : 0);
	// Totals over every page (null above the server's resolve limit: unknown, not the page count).
	const eligibleTotal = $derived(resolved?.eligible_total ?? null);
	const excludedTotal = $derived(resolved?.excluded_total ?? null);
	const excludedReasons = $derived(
		Object.entries(resolved?.excluded_by_reason ?? {}).sort((a, b) => b[1] - a[1])
	);

	const summaryGroups = $derived.by(() => {
		if (!resolved) return [];
		const s = resolved.summary;
		const groups: [string, Record<string, number>][] = [
			['Models', s.models],
			['Apps', s.apps],
			['Harnesses', s.harnesses],
			['Modes', s.modes],
			['Stacks', s.stacks]
		];
		return groups
			.map(([title, map]) => ({
				title,
				entries: Object.entries(map ?? {}).sort((a, b) => b[1] - a[1])
			}))
			.filter((g) => g.entries.length > 0);
	});
</script>

<div class="space-y-4">
	<div class="space-y-2">
		<span class="section-label block" id="target-source-label">Source</span>
		<Segmented
			aria-labelledby="target-source-label"
			value={source}
			items={SOURCES}
			onValueChange={pickSource}
			{disabled}
		/>
	</div>

	{#if !options}
		<p class="text-sm text-muted-foreground">Loading target options…</p>
	{:else if source === 'experiment'}
		{#if options.experiments.length === 0}
			<EmptyState
				compact
				icon={FlaskConical}
				title="No experiments"
				description="Run an experiment first, or pick applications or a filter."
			/>
		{:else}
			<div class="space-y-4">
				<div class="max-w-xl space-y-1.5">
					<Label for="target-experiment">Experiment</Label>
					<Select.Simple
						id="target-experiment"
						value={experimentId}
						placeholder="Choose an experiment…"
						items={options.experiments.map((e) => ({
							value: e.id,
							label: e.name,
							description: `${e.completed_jobs}/${e.total_jobs} jobs completed · ${e.mode} · ${e.status}`
						}))}
						onValueChange={pickExperiment}
						aria-invalid={fieldErrors['target_spec.experiment_id'] ? true : undefined}
						{disabled}
					/>
				</div>
				{#if experiment}
					<div class="grid gap-4 lg:grid-cols-2">
						<CheckList
							legend="Conditions"
							columns={1}
							items={experiment.conditions.map((c) => ({
								value: String(c.id),
								label: c.label,
								hint: c.model_id ?? c.harness ?? ''
							}))}
							bind:selected={conditionIds}
							emptyMeans="every condition"
							onchange={emit}
							{disabled}
						/>
						<CheckList
							legend="Apps"
							columns={2}
							items={experiment.app_slugs.map((slug) => ({
								value: slug,
								label: slug
							}))}
							bind:selected={experimentApps}
							emptyMeans="every app"
							onchange={emit}
							{disabled}
						/>
					</div>
				{/if}
			</div>
		{/if}
	{:else if source === 'batch'}
		{#if options.batches.length === 0}
			<EmptyState
				compact
				icon={Layers}
				title="No batches"
				description="Generate a batch first, or use another source."
			/>
		{:else}
			<div class="max-w-xl space-y-1.5">
				<Label for="target-batch">Batch</Label>
				<Select.Simple
					id="target-batch"
					value={batchId}
					placeholder="Choose a batch…"
					items={options.batches.map((b) => ({
						value: b.id,
						label: b.name,
						description: `${b.completed_jobs}/${b.total_jobs} jobs completed · ${b.mode} · ${b.status}`
					}))}
					onValueChange={(v) => {
						batchId = v;
						emit();
					}}
					aria-invalid={fieldErrors['target_spec.batch_id'] ? true : undefined}
					{disabled}
				/>
			</div>
		{/if}
	{:else if source === 'jobs'}
		<JobPicker bind:selected={jobIds} onchange={emit} {disabled} />
	{:else}
		<div class="space-y-4">
			<p class="flex items-start gap-2 text-xs text-muted-foreground">
				<Info class="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
				Every completed job matching all ticked filters ({options.completed_jobs}
				completed jobs in total). Leave a group empty to not filter on it.
			</p>
			<div class="grid gap-5 lg:grid-cols-2">
				<CheckList
					legend="Models"
					columns={1}
					items={options.models.map((m) => ({
						value: m.model_id,
						label: m.name || m.model_id,
						hint: `${m.jobs} jobs`
					}))}
					bind:selected={fModels}
					emptyMeans="every model"
					onchange={emit}
					{disabled}
				/>
				<CheckList
					legend="Apps"
					columns={2}
					items={options.apps.map((a) => ({
						value: a.slug,
						label: a.name || a.slug,
						hint: `${a.jobs}`
					}))}
					bind:selected={fApps}
					emptyMeans="every app"
					onchange={emit}
					{disabled}
				/>
				{#if options.harnesses.length > 0}
					<CheckList
						legend="Harnesses"
						columns={2}
						items={options.harnesses.map((h) => ({
							value: h.name,
							label: h.name,
							hint: `${h.jobs}`
						}))}
						bind:selected={fHarnesses}
						emptyMeans="every harness"
						onchange={emit}
						{disabled}
					/>
				{/if}
				<CheckList
					legend="Modes"
					columns={2}
					items={options.modes.map((m) => ({
						value: m.mode,
						label: m.mode,
						hint: `${m.jobs}`
					}))}
					bind:selected={fModes}
					emptyMeans="every mode"
					onchange={emit}
					{disabled}
				/>
				<CheckList
					legend="Stacks"
					columns={2}
					items={options.stacks.map((s) => ({
						value: s.slug,
						label: s.slug,
						hint: `${s.jobs}`
					}))}
					bind:selected={fStacks}
					emptyMeans="every stack"
					onchange={emit}
					{disabled}
				/>
				<fieldset class="min-w-0 space-y-2" {disabled}>
					<legend class="section-label mb-2">Created</legend>
					<div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
						<div class="space-y-1">
							<Label class="text-xs" for="target-after">From</Label>
							<Input id="target-after" type="date" bind:value={fAfter} onchange={emit} />
						</div>
						<div class="space-y-1">
							<Label class="text-xs" for="target-before">Until</Label>
							<Input id="target-before" type="date" bind:value={fBefore} onchange={emit} />
						</div>
					</div>
				</fieldset>
			</div>
		</div>
	{/if}

	{#if specErrors.length > 0}
		<ul class="space-y-0.5 text-xs text-danger-fg" role="alert">
			{#each specErrors as message (message)}
				<li>{message}</li>
			{/each}
		</ul>
	{/if}

	<!-- Live resolution -->
	<Card.Root class="gap-3">
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2">
			<div class="space-y-1">
				<Card.Title class="flex items-center gap-2">
					Resolved targets
					{#if resolving}
						<LoaderCircle
							class="h-3.5 w-3.5 animate-spin text-muted-foreground motion-reduce:animate-none"
							aria-label="Resolving"
						/>
					{/if}
				</Card.Title>
				<Card.Description>
					Completed jobs the spec selects, checked against the exclusion rules and the profile's
					scope.
				</Card.Description>
			</div>
			{#if resolved}
				<dl class="flex flex-wrap gap-x-5 gap-y-1 text-sm" aria-live="polite">
					<div class="flex items-baseline gap-1.5">
						<dt class="text-xs text-muted-foreground">Targets</dt>
						<dd class="font-display text-lg font-semibold tabular-nums">
							{resolved.total}
						</dd>
					</div>
					<div class="flex items-baseline gap-1.5">
						<dt class="text-xs text-muted-foreground">
							Eligible{eligibleTotal == null && resolved.pages > 1 ? ' (this page)' : ''}
						</dt>
						<dd class="font-display text-lg font-semibold tabular-nums text-success-fg">
							{#if eligibleTotal != null}{eligibleTotal}{:else}{resolved.eligible_on_page}{/if}
						</dd>
					</div>
					<div class="flex items-baseline gap-1.5">
						<dt class="text-xs text-muted-foreground">
							Excluded{excludedTotal == null && resolved.pages > 1 ? ' (this page)' : ''}
						</dt>
						<dd
							class="font-display text-lg font-semibold tabular-nums"
							class:text-danger-fg={(excludedTotal ?? excludedOnPage) > 0}
						>
							{excludedTotal ?? excludedOnPage}
						</dd>
					</div>
					{#if eligibleTotal == null && resolved.pages > 1 && resolved.eligible_total_limit}
						<p class="w-full text-[11px] text-muted-foreground">
							Totals over all pages are counted up to {resolved.eligible_total_limit} jobs; the study
							preflight counts every target.
						</p>
					{:else if excludedReasons.length > 0}
						<p class="w-full text-[11px] text-muted-foreground">
							Excluded: {excludedReasons
								.map(([code, n]) => `${exclusionLabel(code)} ${n}`)
								.join(' · ')}
						</p>
					{/if}
				</dl>
			{/if}
		</Card.Header>
		<Card.Content class="space-y-3 px-0">
			{#if !spec}
				<div class="px-4">
					<EmptyState
						compact
						title="Choose targets"
						description={source === 'jobs'
							? 'Tick at least one application.'
							: 'Pick a source above to preview its targets.'}
					/>
				</div>
			{:else if resolveError}
				<div class="px-4"><ErrorAlert message={resolveError} /></div>
			{:else if resolved}
				{#if resolved.notes.length > 0}
					<ul class="space-y-1 px-4 text-xs text-warning-fg">
						{#each resolved.notes as note, i (i)}
							<li>{note.detail}</li>
						{/each}
					</ul>
				{/if}
				{#if resolved.total > 0}
					<div class="flex flex-wrap gap-x-6 gap-y-2 px-4 text-xs">
						{#each summaryGroups as group (group.title)}
							<div class="min-w-0">
								<span class="section-label">{group.title}</span>
								<ul class="mt-1 flex flex-wrap gap-1">
									{#each group.entries as [name, n] (name)}
										<li class="mono-tag gap-1">
											{name}<span class="text-muted-foreground">{n}</span>
										</li>
									{/each}
								</ul>
							</div>
						{/each}
					</div>
				{/if}
				{#if resolved.pages > 1}
					<p class="px-4 text-[11px] text-muted-foreground">
						Eligibility is resolved per page here; the preflight checks every target.
					</p>
				{/if}
				<div class="border-t border-border">
					<TargetTable targets={resolved.items} />
					<PaginationBar
						resultsText="{resolved.total} target{resolved.total === 1 ? '' : 's'}"
						page={resolved.page}
						pages={resolved.pages}
						onGoToPage={(p) => (page = p)}
					/>
				</div>
			{:else}
				<p class="px-4 text-sm text-muted-foreground">Resolving…</p>
			{/if}
		</Card.Content>
	</Card.Root>
</div>
