<script lang="ts">
	/**
	 * CellDetailView — one resolved cell, fully pinned (spec 10.2 cell sampler, 10.1 cell inspector): its
	 * levels with pinned refs, the stimuli per role with every source span highlighted by owner (clicking a
	 * span of an editable owner calls `onedit`), the effective roles per harness ("system: ignored
	 * (harness_native)" and the captured native prompt), the effective parameters with their knob class,
	 * model effects, harness, identity keys and the estimate, plus a diff against another cell.
	 * Used by `ResolvedCellDrawer` (builder) and `/experiments/[id]/cells/[cell]`.
	 *
	 * Props: `studyId`, `planHash`, `cellKey`, `compareKeys` (cell keys offered for the diff),
	 * `onedit(span, role)` (omit for read-only), `preloaded` (a detail already in hand, e.g. from
	 * `POST /studies/dry-run/`: nothing is fetched and the diff is hidden, since that plan is not kept).
	 */
	import { untrack } from 'svelte';
	import { diffPlanCells, getPlanCell } from '$lib/api/v3/studies';
	import type { CellDiffOut, FactorKey, ResolvedCellDetailOut, SourceSpanOut } from '$lib/api/v3/types';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Select from '$lib/components/ui/select';
	import { Label } from '$lib/components/ui/label';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { PinnedRefBadge, StimulusText, VersionDiff } from '$lib/components/library';
	import { cn } from '$lib/utils';
	import { formatSeconds, formatTokenCount, formatUsd, shortHash } from '../format';
	import { FACTOR_BY_KEY } from './design';
	import { EDITABLE_OWNERS } from './spans';

	interface Props {
		studyId: string;
		planHash: string;
		cellKey: string;
		compareKeys?: string[];
		onedit?: (span: SourceSpanOut, role: string) => void;
		preloaded?: ResolvedCellDetailOut | null;
	}

	let { studyId, planHash, cellKey, compareKeys = [], onedit, preloaded = null }: Props = $props();

	let detail = $state<ResolvedCellDetailOut | null>(null);
	let error = $state('');
	let loading = $state(false);
	let role = $state('');
	let against = $state('');
	let diff = $state<CellDiffOut | null>(null);
	let diffError = $state('');
	let controller: AbortController | null = null;

	async function load() {
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		loading = true;
		error = '';
		try {
			const out = await getPlanCell(studyId, planHash, cellKey, { signal: ctrl.signal });
			if (ctrl.signal.aborted) return;
			detail = out;
			if (!out.stimuli.some((s) => s.role === role)) role = out.stimuli.find((s) => s.role === 'user')?.role ?? out.stimuli[0]?.role ?? '';
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the cell.');
		} finally {
			if (controller === ctrl) loading = false;
		}
	}

	$effect(() => {
		void studyId;
		void planHash;
		void cellKey;
		const given = preloaded;
		if (given) {
			untrack(() => {
				detail = given;
				error = '';
				if (!given.stimuli.some((s) => s.role === role))
					role = given.stimuli.find((s) => s.role === 'user')?.role ?? given.stimuli[0]?.role ?? '';
			});
			return;
		}
		untrack(() => {
			against = '';
			diff = null;
			void load();
		});
		return () => controller?.abort();
	});

	async function loadDiff(other: string) {
		against = other;
		diff = null;
		diffError = '';
		if (!other) return;
		try {
			diff = await diffPlanCells(studyId, planHash, cellKey, other);
		} catch (e) {
			diffError = formatApiError(e, 'Could not diff the cells.');
		}
	}

	function libraryKind(key: string) {
		const source = FACTOR_BY_KEY[key as FactorKey]?.source;
		return source?.type === 'library' ? source.kind : null;
	}

	function onspan(span: SourceSpanOut) {
		if (onedit && EDITABLE_OWNERS.has(span.owner_kind)) onedit(span, role);
	}

	const CLASS_TONE: Record<string, string> = {
		C: 'border-primary/40 text-primary',
		R: 'border-primary/40 text-primary',
		O: 'border-border text-muted-foreground',
		S: 'border-border text-foreground',
	};
	const CLASS_TITLE: Record<string, string> = {
		C: 'Confound: affects the treatment (fingerprint)',
		R: 'Resource limit that affects the treatment (fingerprint)',
		O: 'Operational: does not change what the model sees',
		S: 'Study design',
	};
	const stimulus = $derived(detail?.stimuli.find((s) => s.role === role) ?? null);
</script>

{#if error}
	<ErrorAlert message={error} onretry={load} />
{:else if !detail}
	<LoadingState label="loading the cell" />
{:else}
	<div class={cn('space-y-5', loading && 'opacity-60')}>
		<section class="space-y-2" aria-labelledby="cell-levels">
			<h3 id="cell-levels" class="section-label">Levels</h3>
			<dl class="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[10rem_1fr]">
				{#each Object.entries(detail.levels) as [key, level] (key)}
					{@const kind = libraryKind(key)}
					<dt class="text-muted-foreground">{FACTOR_BY_KEY[key as FactorKey]?.label ?? key}</dt>
					<dd class="min-w-0 truncate">
						{#if kind}<PinnedRefBadge ref={detail.pinned_refs[key] ?? level} {kind} />{:else}<span class="font-mono">{level}</span>{/if}
					</dd>
				{/each}
			</dl>
		</section>

		<section class="space-y-2" aria-labelledby="cell-stimuli">
			<h3 id="cell-stimuli" class="section-label">Stimuli</h3>
			{#if detail.stimuli.length === 0}
				<p class="text-xs text-muted-foreground">No rendered stimuli.</p>
			{:else}
				<Tabs.Root bind:value={role}>
					<Tabs.List>
						{#each detail.stimuli as s (s.role)}
							<Tabs.Trigger value={s.role}>{s.role} <span class="ml-1 font-mono text-[11px] text-muted-foreground">{formatTokenCount(s.est_tokens)} tok</span></Tabs.Trigger>
						{/each}
					</Tabs.List>
				</Tabs.Root>
				{#if stimulus}
					<StimulusText stimulus={stimulus} onspan={onedit ? onspan : undefined} maxHeight="24rem" />
					<p class="text-[11px] text-muted-foreground">
						sha <span class="font-mono">{shortHash(stimulus.sha)}</span> · {stimulus.chars.toLocaleString()} chars · template
						<span class="font-mono">{stimulus.template_ref.split('#')[0]}</span>
						{#if onedit}· click a highlighted span to edit its owner{/if}
					</p>
				{/if}
			{/if}
		</section>

		{#if detail.effective_roles.length > 0}
			<section class="space-y-2" aria-labelledby="cell-roles">
				<h3 id="cell-roles" class="section-label">Effective roles</h3>
				<ul class="space-y-1 text-xs">
					{#each detail.effective_roles as r (r.role)}
						<li class="flex flex-wrap gap-2">
							<span class="w-20 font-medium">{r.role}</span>
							<span class={r.status === 'used' ? '' : 'text-warning-fg'}>{r.status === 'used' ? 'used' : r.status.replace('(', ' (')}</span>
							<span class="font-mono text-muted-foreground">{r.template_ref.split('#')[0]}</span>
							{#if r.native_prompt_ref}
								<span class="text-muted-foreground">the harness's own system prompt applies: <span class="font-mono">harnesses/captured/{r.native_prompt_ref}.md</span></span>
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/if}

		<section class="space-y-2" aria-labelledby="cell-params">
			<h3 id="cell-params" class="section-label">Effective parameters</h3>
			<div class="max-h-72 overflow-y-auto rounded-md border border-border">
				<table class="w-full text-xs">
					<thead><tr><th>Knob</th><th>Value</th><th>Class</th><th>Source</th></tr></thead>
					<tbody>
						{#each detail.effective_params as p (p.key)}
							<tr>
								<td class="font-mono">{p.key}</td>
								<td class="font-mono">{typeof p.value === 'object' ? JSON.stringify(p.value) : String(p.value)}</td>
								<td><span class={cn('rounded border px-1 font-mono', CLASS_TONE[p.cls])} title={CLASS_TITLE[p.cls]}>{p.cls}</span></td>
								<td class={p.source === 'clamp' ? 'text-warning-fg' : 'text-muted-foreground'}>{p.source}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<dl class="grid gap-x-4 gap-y-1 text-xs sm:grid-cols-[10rem_1fr]">
				<dt class="text-muted-foreground">Seed forwarded</dt>
				<dd>{detail.model_effects.seed_forwarded === null ? '—' : detail.model_effects.seed_forwarded ? 'yes (paired comparisons possible)' : 'no (independent comparisons)'}</dd>
				{#if detail.model_effects.dropped_params.length}
					<dt class="text-muted-foreground">Dropped by the model</dt>
					<dd class="font-mono">{detail.model_effects.dropped_params.join(', ')}</dd>
				{/if}
				{#if detail.harness}
					<dt class="text-muted-foreground">Harness</dt>
					<dd>
						<span class="font-mono">{detail.harness.name} {detail.harness.version}</span>
						{#if detail.harness.protocol_mode}· {detail.harness.protocol_mode}{/if}
						{#if detail.harness.vendor_affinity}· vendor affinity {detail.harness.vendor_affinity}{/if}
					</dd>
					{#if detail.harness.unsupported_knobs.length}
						<dt class="text-muted-foreground">Not enforced by the harness</dt>
						<dd class="font-mono text-warning-fg">{detail.harness.unsupported_knobs.join(', ')}</dd>
					{/if}
				{/if}
				{#if detail.starter_tree.length}
					<dt class="text-muted-foreground">Starter files</dt>
					<dd class="font-mono">{detail.starter_tree.length} ({detail.starter_tree.slice(0, 4).join(', ')}{detail.starter_tree.length > 4 ? ', …' : ''})</dd>
				{/if}
				{#if detail.slot_context}
					<dt class="text-muted-foreground">Slot context</dt>
					<dd>{detail.slot_context.files.length} files · {formatTokenCount(detail.slot_context.tokens)} tok · <span class="font-mono">{shortHash(detail.slot_context.sha)}</span></dd>
				{/if}
			</dl>
		</section>

		<section class="grid gap-4 sm:grid-cols-2">
			<div class="space-y-2">
				<h3 class="section-label">Identity</h3>
				{#if detail.identity}
					<dl class="grid grid-cols-[9rem_1fr] gap-y-0.5 text-xs">
						{#each Object.entries(detail.identity) as [key, value] (key)}
							<dt class="text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
							<dd class="font-mono" title={value}>{shortHash(value)}</dd>
						{/each}
					</dl>
				{:else}
					<p class="text-xs text-muted-foreground">—</p>
				{/if}
			</div>
			<div class="space-y-2">
				<h3 class="section-label">Estimate per attempt</h3>
				{#if detail.estimate}
					<dl class="grid grid-cols-[8rem_1fr] gap-y-0.5 text-xs">
						<dt class="text-muted-foreground">expected</dt><dd class="font-mono">{formatUsd(detail.estimate.expected_usd)}</dd>
						<dt class="text-muted-foreground">p10 – p90</dt><dd class="font-mono">{formatUsd(detail.estimate.p10_usd)} – {formatUsd(detail.estimate.p90_usd)}</dd>
						<dt class="text-muted-foreground">reserved (worst)</dt><dd class="font-mono">{formatUsd(detail.estimate.cap_usd)}</dd>
						<dt class="text-muted-foreground">list price</dt><dd class="font-mono">{formatUsd(detail.estimate.expected_list_usd)}</dd>
						<dt class="text-muted-foreground">tokens</dt><dd class="font-mono">{formatTokenCount(detail.estimate.expected_tokens)}</dd>
						<dt class="text-muted-foreground">wall time</dt><dd class="font-mono">{formatSeconds(detail.estimate.expected_wall_s)} (p90 {formatSeconds(detail.estimate.p90_wall_s)})</dd>
						<dt class="text-muted-foreground">basis</dt><dd class="font-mono">{detail.estimate.basis}</dd>
					</dl>
				{:else}
					<p class="text-xs text-muted-foreground">Not estimated.</p>
				{/if}
			</div>
		</section>

		{#if !preloaded && compareKeys.filter((k) => k !== cellKey).length > 0}
			<section class="space-y-2">
				<Label for="cell-diff">Diff against</Label>
				<Select.Simple
					id="cell-diff"
					size="sm"
					value={against}
					placeholder="Pick another cell"
					items={compareKeys.filter((k) => k !== cellKey).map((k) => ({ value: k, label: k }))}
					onValueChange={loadDiff}
				/>
				{#if diffError}<ErrorAlert message={diffError} />{/if}
				{#if diff}
					{#if Object.keys(diff.levels_diff).length}
						<ul class="text-xs">
							{#each Object.entries(diff.levels_diff) as [key, [a, b]] (key)}
								<li><span class="text-muted-foreground">{key}:</span> <span class="font-mono">{a ?? '—'}</span> → <span class="font-mono">{b ?? '—'}</span></li>
							{/each}
						</ul>
					{/if}
					{#each diff.stimuli_diff as d (d.role)}
						<p class="section-label">{d.role}</p>
						<VersionDiff unified={d.unified} />
					{/each}
					{#if diff.params_diff.length}
						<ul class="text-xs">
							{#each diff.params_diff as p (p.key)}
								<li class="font-mono">{p.key}: {JSON.stringify(p.a)} → {JSON.stringify(p.b)}</li>
							{/each}
						</ul>
					{/if}
					{#if !diff.stimuli_diff.length && !diff.params_diff.length && !Object.keys(diff.levels_diff).length}
						<p class="text-xs text-muted-foreground">The two cells are identical apart from their seed and position.</p>
					{/if}
				{/if}
			</section>
		{/if}
	</div>
{/if}
