<script lang="ts">
	/**
	 * ParentPicker — step 0 of the slot picker (spec 7.2, 10.5): the pool of usable, tested `app_agentic` runs
	 * of one task (`GET /slots/parents/`), filtered by stack, model, harness and outcome, with pass rate,
	 * probes exploited, cost and an "already frozen" badge. Parents are drawn at random with a recorded seed
	 * ("Draw k random parents", stratified by parent model by default) or picked by hand (recorded as
	 * `manual`). "Generate a parent app" opens `GenerateParentForm`.
	 *
	 * Props: `task` (initial task ref), `onconfirm({runIds, selection, poolSize})`.
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import PaginationBar from '$lib/components/PaginationBar.svelte';
	import { tableFrame } from '$lib/components/table';
	import Dices from '@lucide/svelte/icons/dices';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Users from '@lucide/svelte/icons/users';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { cn } from '$lib/utils';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { drawSlotParents, listSlotParents } from '$lib/api/v3/slots';
	import type { Page, ParentSelection, SlotParentRow } from '$lib/api/v3/types';
	import { fmtRate, fmtUsd, shortSha } from '$lib/components/runs/format';
	import ParentDrawForm from './ParentDrawForm.svelte';
	import GenerateParentForm from './GenerateParentForm.svelte';
	import { libraryOptions, type OptionItem } from './options';
	import {
		emptyParentDrawForm,
		manualSelection,
		toParentSelection,
		validateParentDraw,
		type ParentDrawForm as DrawForm,
	} from './selection';

	export interface ParentPick {
		runIds: string[];
		selection: ParentSelection;
		poolSize: number | null;
	}

	interface Props {
		task?: string;
		onconfirm: (pick: ParentPick) => void;
	}

	let { task = '', onconfirm }: Props = $props();

	let taskRef = $state('');
	let stack = $state('');
	let model = $state('');
	let harness = $state('');
	let outcome = $state<'tested' | 'build_fail' | 'start_fail'>('tested');
	let pageNo = $state(1);
	let data = $state<Page<SlotParentRow> | null>(null);
	let loading = $state(false);
	let error = $state('');
	let taskItems = $state<OptionItem[]>([]);
	let stackItems = $state<OptionItem[]>([]);

	let picked = $state<Set<string>>(new Set());
	let drawForm = $state<DrawForm>(emptyParentDrawForm());
	/** A draw keeps the exact selection that produced it: that is what the frozen SlotSets record. */
	interface Drawn {
		parents: string[];
		seed: number;
		pool_size: number;
		selection: ParentSelection;
		key: string;
	}
	let lastDraw = $state<Drawn | null>(null);
	let drawToken = 0;
	const drawKey = $derived(JSON.stringify(toParentSelection(drawForm)));
	// Any edit to the draw form (k, stratification, min pass rate, studies, seed, filters) retires the draw shown:
	// its parents were not produced by the form as it is now.
	const drawn = $derived(lastDraw && lastDraw.key === drawKey ? lastDraw : null);
	let drawing = $state(false);
	let drawError = $state('');
	let generateOpen = $state(false);

	$effect(() => {
		if (task && !taskRef) taskRef = task;
	});

	$effect(() => {
		libraryOptions('task', true)
			.then((items) => (taskItems = items))
			.catch(() => (taskItems = []));
		libraryOptions('stack')
			.then((items) => (stackItems = items))
			.catch(() => (stackItems = []));
	});

	$effect(() => {
		const query = {
			task: taskRef || undefined,
			stack: stack || undefined,
			model: model || undefined,
			harness: harness || undefined,
			outcome,
			page: pageNo,
			page_size: 50,
		};
		const controller = new AbortController();
		loading = true;
		listSlotParents(query, { signal: controller.signal })
			.then((out) => {
				data = out;
				error = '';
			})
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the parent pool.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	// A new pool invalidates a draw and the manual picks.
	$effect(() => {
		void [taskRef, stack, model, harness, outcome];
		lastDraw = null;
		picked = new Set();
		const patch = { task: taskRef, stack, models: model ? [model] : [], harnesses: harness ? [harness] : [] };
		drawForm = { ...untrack(() => drawForm), ...patch };
	});

	const rows = $derived(data?.items ?? []);
	const poolSize = $derived(data?.total ?? null);
	const drawErrors = $derived(validateParentDraw(drawForm, poolSize));
	const drawnSet = $derived(new Set(drawn?.parents ?? []));
	const modelItems = $derived(
		[...new Set(rows.map((r) => r.model).filter(Boolean) as string[])].sort().map((m) => ({ value: m, label: m })),
	);
	const harnessItems = $derived(
		[...new Set(rows.map((r) => r.harness).filter(Boolean) as string[])].sort().map((h) => ({ value: h, label: h })),
	);
	const pages = $derived(data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1);

	function togglePick(id: string, on: boolean) {
		const next = new Set(picked);
		if (on) next.add(id);
		else next.delete(id);
		picked = next;
	}

	async function draw() {
		const selection = toParentSelection(drawForm);
		const key = JSON.stringify(selection);
		const token = ++drawToken;
		drawing = true;
		drawError = '';
		lastDraw = null;
		try {
			const out = await drawSlotParents(selection);
			if (token !== drawToken) return;
			lastDraw = { parents: out.parents, seed: out.seed, pool_size: out.pool_size, selection, key };
			if (out.parents.length === 0) drawError = 'The pool is empty for these filters.';
		} catch (e) {
			if (token === drawToken) drawError = formatApiError(e, 'Could not draw parents.');
		} finally {
			if (token === drawToken) drawing = false;
		}
	}

	function useDrawn() {
		if (!drawn) return;
		onconfirm({
			runIds: drawn.parents,
			selection: { ...drawn.selection, seed: drawn.seed },
			poolSize: drawn.pool_size,
		});
	}

	function usePicked() {
		onconfirm({ runIds: [...picked], selection: manualSelection(taskRef, picked.size), poolSize });
	}

	const parentModels = $derived(new Set(rows.filter((r) => drawnSet.has(r.run_id)).map((r) => r.model)).size);
</script>

<div class="space-y-4">
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="border-b border-border py-3">
			<Card.Title class="font-display">Parent pool</Card.Title>
			<Card.Description>
				Completed agentic apps whose evaluation ran (outcome tested). Recommended: the same part from ≥ 3 parents
				written by ≥ 2 models, drawn at random.
			</Card.Description>
		</Card.Header>
		<div class="table-toolbar flex-wrap gap-2">
			<div class="flex flex-wrap items-center gap-2">
				<Select.Simple
					size="sm"
					aria-label="Task"
					bind:value={taskRef}
					placeholder="Task (required to draw)"
					clearLabel="Any task"
					items={taskItems}
				/>
				<Select.Simple size="sm" aria-label="Stack" bind:value={stack} placeholder="Any stack" clearLabel="Any stack" items={stackItems} />
				<Select.Simple size="sm" aria-label="Model" bind:value={model} placeholder="Any model" clearLabel="Any model" items={modelItems} />
				<Select.Simple size="sm" aria-label="Harness" bind:value={harness} placeholder="Any harness" clearLabel="Any harness" items={harnessItems} />
				<Segmented
					size="sm"
					aria-label="Outcome"
					value={outcome}
					onValueChange={(v) => (outcome = v as typeof outcome)}
					items={[
						{ value: 'tested', label: 'Tested', title: 'The default: parents whose tester ran' },
						{ value: 'build_fail', label: 'Build failed', title: 'Allowed only with "allow unbuilt parent": no runtime slot tests, never confirmatory' },
						{ value: 'start_fail', label: 'Start failed', title: 'Allowed only with "allow unbuilt parent": no runtime slot tests, never confirmatory' },
					]}
				/>
			</div>
			<Button variant="outline" size="sm" onclick={() => (generateOpen = true)}>
				<Sparkles class="mr-1.5 h-3.5 w-3.5" />Generate a parent app
			</Button>
		</div>

		{#if error}
			<div class="p-4"><ErrorAlert message={error} /></div>
		{:else if loading && !data}
			<LoadingState label="loading parent pool" />
		{:else if rows.length === 0}
			<EmptyState
				compact
				icon={Users}
				title="No parent in the pool"
				description={taskRef
					? 'No tested app of this task matches. Generate one, or relax the filters.'
					: 'No tested agentic app yet. Generate a parent app (frugal, one run) to start.'}
			>
				{#snippet action()}
					<Button size="sm" onclick={() => (generateOpen = true)}><Sparkles class="mr-1.5 h-3.5 w-3.5" />Generate a parent app</Button>
				{/snippet}
			</EmptyState>
		{:else}
			<div class="table-frame" {@attach tableFrame}>
				<table>
					<thead>
						<tr>
							<th class="w-8"><span class="sr-only">Pick</span></th>
							<th>Parent run</th>
							<th>Task</th>
							<th>Stack</th>
							<th>Model</th>
							<th>Harness</th>
							<th class="num" title="Agent checks only">Pass rate</th>
							<th class="num">Probes exploited</th>
							<th class="num">Cost</th>
							<th>Frozen</th>
						</tr>
					</thead>
					<tbody>
						{#each rows as row (row.run_id)}
							<tr data-selected={picked.has(row.run_id) || drawnSet.has(row.run_id) ? 'true' : undefined} class={cn(drawnSet.has(row.run_id) && 'bg-primary/5')}>
								<td>
									<Checkbox
										aria-label={`Pick ${shortSha(row.run_id, 8)}`}
										checked={picked.has(row.run_id)}
										onCheckedChange={(v) => togglePick(row.run_id, v === true)}
									/>
								</td>
								<td>
									<a class="font-mono text-xs text-primary hover:underline" href={`/samples/${row.run_id}`} target="_blank" rel="noopener">{shortSha(row.run_id, 8)}</a>
									{#if drawnSet.has(row.run_id)}<Badge variant="default" class="ml-1">drawn</Badge>{/if}
								</td>
								<td class="font-mono text-xs">{row.task ?? '—'}</td>
								<td class="font-mono text-xs">{row.stack ?? '—'}</td>
								<td class="font-mono text-xs">{row.model ?? '—'}</td>
								<td class="font-mono text-xs">{row.harness ?? '—'}</td>
								<td class="num">{fmtRate(row.agent_pass_rate)}</td>
								<td class="num">{row.probes_exploited ?? '—'}</td>
								<td class="num">{fmtUsd(row.cost_usd)}</td>
								<td>
									{#if row.frozen}<Badge variant="outline" title="Already frozen as a SlotSet">{row.slot_sets} set{row.slot_sets === 1 ? '' : 's'}</Badge>{:else}<span class="text-muted-foreground">—</span>{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			<PaginationBar
				class="border-t border-border"
				resultsText={`${rows.length} of ${data?.total ?? 0} in the pool`}
				page={pageNo}
				{pages}
				onGoToPage={(p) => (pageNo = p)}
			/>
		{/if}
	</Card.Root>

	<div class="grid gap-4 lg:grid-cols-2">
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2 font-display"><Dices class="h-4 w-4" />Draw parents at random</Card.Title>
				<Card.Description>The seed and the pool query are recorded on every frozen parent (and in the manifests).</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				<ParentDrawForm bind:form={drawForm} errors={drawErrors} {poolSize} />
				{#if drawError}<ErrorAlert message={drawError} />{/if}
				<div class="flex flex-wrap items-center gap-2">
					<Button variant="outline" size="sm" disabled={drawing || Object.keys(drawErrors).length > 0} onclick={draw}>
						{#if drawing}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Dices class="mr-1.5 h-3.5 w-3.5" />{/if}
						Draw {drawForm.k ?? 0} random parent{drawForm.k === 1 ? '' : 's'}
					</Button>
					{#if drawn && drawn.parents.length}
						<span class="text-xs text-muted-foreground">
							{drawn.parents.length} of {drawn.pool_size} · seed <span class="font-mono">{drawn.seed}</span>
							{#if parentModels > 0}· {parentModels} parent model{parentModels === 1 ? '' : 's'} on this page{/if}
						</span>
						<Button size="sm" class="ml-auto" onclick={useDrawn}>Freeze the drawn parents</Button>
					{/if}
				</div>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title class="font-display">Pick by hand</Card.Title>
				<Card.Description>
					Recorded as a manual selection. Confirmatory studies with manual or single parents need the estimand
					"conditional on parent".
				</Card.Description>
			</Card.Header>
			<Card.Content class="flex flex-wrap items-center gap-2">
				<span class="text-sm">{picked.size} picked</span>
				{#if picked.size > 0}
					<Button variant="ghost" size="sm" onclick={() => (picked = new Set())}>Clear</Button>
				{/if}
				<Button size="sm" class="ml-auto" disabled={picked.size === 0} onclick={usePicked}>
					Freeze {picked.size || ''} picked parent{picked.size === 1 ? '' : 's'}
				</Button>
			</Card.Content>
		</Card.Root>
	</div>
</div>

<GenerateParentForm bind:open={generateOpen} task={taskRef} />
