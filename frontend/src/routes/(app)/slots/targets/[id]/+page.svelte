<script lang="ts">
	// Slot target page (spec 10.1): the original part (variant 0, a reference point, not a control) against
	// its variants — outcome, parse status, cluster, features and slot pass rate per variant, the diff of a
	// chosen variant — plus the validation detail (original 3×, holed 1×, discriminating / dropped / flaky
	// checks, probe stability) and the leak report. Lines of a variant's file can seed a detector.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import { tableFrame, TableToolbar } from '$lib/components/table';
	import Copy from '@lucide/svelte/icons/copy';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSampleFileContent } from '$lib/api/v3/samples';
	import { getSlotTarget, getSlotVariantDiff, listSlotVariants, matchSlotTarget, revalidateSlotTarget } from '$lib/api/v3/slots';
	import type { Page, SlotTargetDetailOut, SlotVariantDiffOut, SlotVariantRow } from '$lib/api/v3/types';
	import CodeViewer from '$lib/components/runs/CodeViewer.svelte';
	import { fmtRate, OUTCOME_TONES, outcomeLabel, shortSha } from '$lib/components/runs/format';
	import { VALIDATION_TONES, validationLabel } from '$lib/components/slots';
	import { DetectorFromSelection, FEATURE_STATUS_TEXT, splitFeatureKey } from '$lib/components/patterns';

	const targetId = $derived(page.params.id ?? '');
	const study = $derived(page.url.searchParams.get('study') ?? '');
	const currentPage = $derived(Math.max(1, Number(page.url.searchParams.get('page') ?? '1') || 1));

	let target = $state<SlotTargetDetailOut | null>(null);
	let loading = $state(true);
	let error = $state('');
	let variants = $state<Page<SlotVariantRow> | null>(null);
	let variantsError = $state('');
	let studyInput = $state('');
	let busy = $state(false);

	function setParam(key: string, value: string) {
		const url = new URL(page.url);
		if (value) url.searchParams.set(key, value);
		else url.searchParams.delete(key);
		if (key !== 'page') url.searchParams.delete('page');
		goto(`${url.pathname}${url.search}`, { replaceState: true, keepFocus: true, noScroll: true });
	}

	async function loadTarget(id: string, signal?: AbortSignal) {
		loading = untrack(() => target === null);
		try {
			target = await getSlotTarget(id, { signal });
			error = '';
		} catch (e) {
			if (!isAbortError(e)) error = formatApiError(e, 'Could not load the target.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const controller = new AbortController();
		void loadTarget(targetId, controller.signal);
		return () => controller.abort();
	});

	$effect(() => {
		studyInput = study;
	});

	$effect(() => {
		const controller = new AbortController();
		listSlotVariants(targetId, { study: study || undefined, page: currentPage, page_size: 50 }, { signal: controller.signal })
			.then((out) => {
				variants = out;
				variantsError = '';
			})
			.catch((e) => {
				if (!isAbortError(e)) variantsError = formatApiError(e, 'Could not load the variants.');
			});
		return () => controller.abort();
	});

	$effect(() => {
		if (target?.validation?.status !== 'pending') return;
		const timer = setInterval(() => void loadTarget(targetId), 10_000);
		return () => clearInterval(timer);
	});

	// ── one variant: diff vs original, file selection → detector ────────────
	let chosen = $state<string | null>(null);
	let diff = $state<SlotVariantDiffOut | null>(null);
	let fileText = $state<string | null>(null);
	let view = $state<'diff' | 'replacement' | 'file'>('diff');
	let diffError = $state('');
	let selected = $state<{ start_line: number; end_line: number } | null>(null);
	let detectorOpen = $state(false);

	async function choose(runId: string) {
		chosen = runId;
		diff = null;
		fileText = null;
		selected = null;
		diffError = '';
		view = 'diff';
		try {
			diff = await getSlotVariantDiff(runId);
		} catch (e) {
			diffError = formatApiError(e, 'Could not load the variant.');
		}
	}

	async function showFile() {
		view = 'file';
		if (fileText !== null || !diff || !chosen) return;
		try {
			fileText = await getSampleFileContent(chosen, diff.path);
		} catch (e) {
			diffError = formatApiError(e, 'Could not load the file.');
		}
	}

	async function act(action: () => Promise<unknown>, done: string) {
		busy = true;
		try {
			await action();
			toast.success(done);
			await loadTarget(targetId);
		} catch (e) {
			toast.error(formatApiError(e));
		} finally {
			busy = false;
		}
	}

	const v = $derived(target?.validation ?? null);
	const checks = $derived(Object.keys(v?.original_pass ?? {}).sort());
	const discriminating = $derived(new Set(v?.discriminating_checks ?? []));
	const pages = $derived(variants ? Math.max(1, Math.ceil(variants.total / variants.page_size)) : 1);
</script>

<div class="space-y-4">
	<PageHeader
		title={target?.label || (target ? `Target ${shortSha(target.id, 8)}` : 'Slot target')}
		description="One part of a frozen parent, regenerated N times; the original is variant 0 (a reference point, not a control)."
		back={target ? { href: `/slots/${target.slot_set}`, label: 'Slot set' } : { href: '/slots/new', label: 'Slot picker' }}
	>
		{#snippet meta()}
			{#if target}
				<Badge variant="outline">{target.kind}</Badge>
				<StatusBadge status={target.validation?.status ?? 'pending'} tones={VALIDATION_TONES} label={validationLabel(target)} />
				{#if !target.usable && target.unusable_reason}<Badge variant="secondary" title={target.unusable_reason}>not usable</Badge>{/if}
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if target}
				{#if target.match_key}
					<Button variant="outline" size="sm" disabled={busy} onclick={() => act(() => matchSlotTarget(target!.id, 'same_task'), 'Matching parts created in the other parents of this task')}>
						<Copy class="mr-1.5 h-3.5 w-3.5" />Add matching part from other parents
					</Button>
				{/if}
				{#if target.validation?.status === 'error' || target.validation?.status === 'uninformative'}
					<Button variant="outline" size="sm" disabled={busy} onclick={() => act(() => revalidateSlotTarget(target!.id), 'Validation queued again')}>
						<RotateCw class="mr-1.5 h-3.5 w-3.5" />Revalidate
					</Button>
				{/if}
			{/if}
		{/snippet}
		{#if target}
			<div class="header-stats">
				<span class="header-stat"><b class="font-mono">{target.address.path}</b> path</span>
				{#if target.address.span}<span class="header-stat"><b>{target.address.span.start_line}–{target.address.span.end_line}</b> lines</span>{/if}
				<span class="header-stat"><b>{target.default_hole_mode}</b> default hole</span>
				<span class="header-stat"><b>{target.variants_summary.n}</b> variants</span>
				<span class="header-stat"><b>{v?.status === 'uninformative' ? 'n/a' : fmtRate(target.original?.slot_pass_rate ?? null, '—')}</b> original slot pass rate</span>
				<span class="header-stat font-mono" title={target.target_hash}>{shortSha(target.target_hash)}</span>
			</div>
		{/if}
	</PageHeader>

	{#if error}<ErrorAlert message={error} onretry={() => loadTarget(targetId)} />{/if}
	{#if loading}
		<LoadingState label="loading target" />
	{:else if target}
		<div class="grid gap-4 xl:grid-cols-2">
			<Card.Root>
				<Card.Header>
					<Card.Title class="font-display">Validation</Card.Title>
					<Card.Description>
						Original evaluated 3×, holed tree 1×. A check is kept only if it passes 3/3 on the original and fails with the hole.
						{#if v?.at}Validated {v.at.slice(0, 16).replace('T', ' ')}.{/if}
					</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3 text-xs">
					{#if v?.error}<ErrorAlert message={v.error} />{/if}
					{#if checks.length === 0}
						<p class="text-muted-foreground">{v?.status === 'pending' ? 'Validation runs are queued on the eval lane.' : 'No linked check was evaluated.'}</p>
					{:else}
						<div class="overflow-x-auto">
							<table>
								<thead><tr><th>Check</th><th>Original ×3</th><th>With the hole</th><th>Kept</th></tr></thead>
								<tbody>
									{#each checks as check (check)}
										<tr>
											<td class="font-mono">{check}</td>
											<td class="font-mono">{(v?.original_pass[check] ?? []).map((b) => (b ? 'pass' : 'fail')).join(' · ')}</td>
											<td class="font-mono">{v?.holed_fail[check] === undefined ? '—' : v.holed_fail[check] ? 'fails' : 'still passes'}</td>
											<td>
												{#if discriminating.has(check)}<StatusBadge status="completed" label="discriminating" />
												{:else if v?.flaky_checks.includes(check)}<StatusBadge status="warning" label="flaky" />
												{:else}<StatusBadge status="cancelled" label="dropped" />{/if}
											</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
					{#if Object.keys(v?.probe_stability ?? {}).length}
						<div>
							<p class="section-label">Probe stability</p>
							<ul class="space-y-0.5">
								{#each Object.entries(v?.probe_stability ?? {}) as [probe, statuses] (probe)}
									<li><span class="font-mono">{probe}</span>: {statuses.join(' · ')}</li>
								{/each}
							</ul>
						</div>
					{/if}
					{#if v?.runs?.length}
						<p class="text-muted-foreground">
							Validation runs: {#each v.runs as r, i (r)}{i ? ', ' : ''}<a class="font-mono text-primary hover:underline" href={`/samples/${r}`}>{shortSha(r, 6)}</a>{/each}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title class="font-display">Leak and links</Card.Title>
					<Card.Description>Leak score: share of the original's 8-token shingles found in the context (and the agent workspace).</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3 text-xs">
					<div class="flex flex-wrap items-center gap-2">
						<StatusBadge
							status={target.leak.matches.some((m) => m.verbatim) ? 'error' : target.leak.score > 0.3 ? 'warning' : 'completed'}
							label={`leak ${target.leak.score.toFixed(2)}`}
						/>
						{#each target.leak.matches as m (m.path)}
							<span class="font-mono">{m.path} · {m.shingles}{m.verbatim ? ' · verbatim' : ''}</span>
						{/each}
					</div>
					<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
						<dt class="text-muted-foreground">linked endpoints</dt><dd class="font-mono">{target.linked_endpoints.join(', ') || '—'}</dd>
						<dt class="text-muted-foreground">linked checks</dt>
						<dd class="font-mono">{target.linked_checks.join(', ') || '—'} {#if target.linked_checks_source === 'edited'}<Badge variant="secondary">edited</Badge>{/if}</dd>
						<dt class="text-muted-foreground">linked probes</dt><dd class="font-mono">{target.linked_probes.join(', ') || '—'}</dd>
						<dt class="text-muted-foreground">allowlist</dt><dd class="font-mono">{target.allowlist.join(', ')}</dd>
						<dt class="text-muted-foreground">edit policy</dt><dd>{target.edit_policy}</dd>
						<dt class="text-muted-foreground">context</dt><dd>{target.default_context_policy.mode} · {target.default_context_policy.max_context_tokens} tokens</dd>
						{#if target.interface.signature}<dt class="text-muted-foreground">signature</dt><dd class="font-mono">{target.interface.signature}</dd>{/if}
						{#if target.original}
							<dt class="text-muted-foreground">original run</dt>
							<dd><a class="font-mono text-primary hover:underline" href={`/samples/${target.original.run_id}`}>{shortSha(target.original.run_id, 8)}</a></dd>
						{/if}
					</dl>
				</Card.Content>
			</Card.Root>
		</div>

		{#if target.original_text !== null}
			<details class="rounded-md border border-border bg-card">
				<summary class="cursor-pointer px-4 py-2.5 text-sm font-medium">The original part ({target.language || 'text'})</summary>
				<div class="px-4 pb-3"><CodeViewer code={target.original_text} language={target.language} path={target.address.path} class="max-h-96" /></div>
			</details>
		{/if}

		<SectionHeading size="sm" title="Variants" count={variants?.total ?? 0} />
		<Card.Root class="gap-0 overflow-clip py-0">
			<TableToolbar>
				{#snippet summary()}
					<span>{Object.entries(target!.variants_summary.by_outcome).map(([o, n]) => `${outcomeLabel(o)} ${n}`).join(' · ') || 'no variants yet'}</span>
				{/snippet}
				{#snippet controls()}
					<form class="flex items-center gap-1" onsubmit={(e) => { e.preventDefault(); setParam('study', studyInput.trim()); }}>
						<Input class="h-8 w-64" placeholder="Filter by experiment id, Enter" aria-label="Experiment id" bind:value={studyInput} />
					</form>
				{/snippet}
			</TableToolbar>
			{#if variantsError}
				<div class="p-4"><ErrorAlert message={variantsError} /></div>
			{:else if !variants}
				<LoadingState label="loading variants" />
			{:else if variants.items.length === 0}
				<EmptyState compact title="No variants" description="Use this target as a unit of a slot experiment to regenerate it." />
			{:else}
				<div class="table-frame" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th>Variant</th>
								<th class="num">Condition</th>
								<th>Outcome</th>
								<th>Parse</th>
								<th class="num">Cluster</th>
								<th class="num" title="Discriminating checks only; n/a for uninformative targets">Slot pass</th>
								<th class="num">+/−</th>
								<th class="num">Similarity</th>
								<th>Features present</th>
							</tr>
						</thead>
						<tbody>
							{#each variants.items as row (row.run_id)}
								{@const present = Object.entries(row.features).filter(([, s]) => s === 'present')}
								{@const errors = Object.entries(row.features).filter(([, s]) => s === 'error')}
								<tr class={cn('cursor-pointer', chosen === row.run_id && 'bg-primary/5')} data-selected={chosen === row.run_id ? 'true' : undefined} onclick={() => choose(row.run_id)}>
									<td><a class="font-mono text-xs text-primary hover:underline" href={`/samples/${row.run_id}`}>{shortSha(row.run_id, 8)}</a>{#if row.repeat_index !== null}<div class="cell-sub">repeat {row.repeat_index}</div>{/if}</td>
									<td class="num">{row.condition ?? '—'}</td>
									<td>{#if row.outcome}<StatusBadge status={row.outcome} tones={OUTCOME_TONES} label={outcomeLabel(row.outcome)} />{:else}—{/if}{#if row.edit_violation}<div class="cell-sub">edit violation</div>{/if}</td>
									<td class="font-mono text-xs">{row.parse_status ?? '—'}</td>
									<td class="num">{row.cluster_id ?? '—'}</td>
									<td class="num">{v?.status === 'uninformative' ? 'n/a' : fmtRate(row.slot_pass_rate, '—')}</td>
									<td class="num text-xs">{row.diff_vs_original ? `+${row.diff_vs_original.lines_added} −${row.diff_vs_original.lines_removed}` : '—'}</td>
									<td class="num text-xs">{row.diff_vs_original ? row.diff_vs_original.similarity.toFixed(2) : '—'}{#if row.diff_vs_original?.normalized_equal}<div class="cell-sub">= original</div>{/if}</td>
									<td class="text-xs">
										{#each present as [key] (key)}<Badge variant="outline" class="mr-1">{splitFeatureKey(key).feature}</Badge>{/each}
										{#if errors.length}<span class="text-danger-fg" title={errors.map(([k]) => k).join(', ')}>{errors.length} {FEATURE_STATUS_TEXT.error}</span>{/if}
										{#if present.length === 0 && errors.length === 0}<span class="text-muted-foreground">—</span>{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<PaginationBar class="border-t border-border" resultsText={`${variants.items.length} of ${variants.total}`} page={currentPage} {pages} onGoToPage={(p) => setParam('page', String(p))} />
			{/if}
		</Card.Root>

		{#if chosen}
			<Card.Root>
				<Card.Header>
					<Card.Title class="font-display">Variant <span class="font-mono">{shortSha(chosen, 8)}</span></Card.Title>
				</Card.Header>
				<Card.Content class="space-y-2">
					{#if diffError}<ErrorAlert message={diffError} />{/if}
					{#if diff}
						<div class="flex flex-wrap items-center gap-2">
							<Segmented
								size="sm"
								aria-label="Variant view"
								value={view}
								onValueChange={(next) => (next === 'file' ? showFile() : (view = next as typeof view))}
								items={[
									{ value: 'diff', label: 'Diff vs original' },
									{ value: 'replacement', label: 'Replacement' },
									{ value: 'file', label: 'File (select lines)' },
								]}
							/>
							{#if view === 'file'}
								<Button variant="outline" size="sm" disabled={!selected} onclick={() => (detectorOpen = true)} title={selected ? undefined : 'Drag over line numbers first'}>
									<WandSparkles class="mr-1.5 h-3.5 w-3.5" />Create detector from selection
								</Button>
							{/if}
						</div>
						{#if view === 'diff'}
							<CodeViewer code={diff.unified || '(identical to the original)'} language="diff" class="max-h-[32rem]" />
						{:else if view === 'replacement'}
							<CodeViewer code={diff.replacement} path={diff.path} class="max-h-[32rem]" />
						{:else if fileText !== null}
							<CodeViewer code={fileText} path={diff.path} selectable highlight={selected} onselectlines={(s, e, done) => { if (done) selected = { start_line: s, end_line: e }; }} class="max-h-[32rem]" />
						{:else}
							<LoadingState label="loading file" class="py-6" />
						{/if}
						<DetectorFromSelection bind:open={detectorOpen} runId={chosen} path={diff.path} span={selected} language={target.language} />
					{:else if !diffError}
						<LoadingState label="loading variant" class="py-6" />
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}
	{/if}
</div>
