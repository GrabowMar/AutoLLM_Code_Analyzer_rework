<script lang="ts">
	// Slot picker (spec 10.5): 0 choose parents (ParentPicker; skipped with ?parent=<run>[,<run>]) → 1 freeze
	// (FreezeStep) → 2 choose parts in a three-pane layout (SlotFileTree | SlotCodePane | SlotTargetForm) for
	// each frozen parent (?sets=<id>,…&set=<active>) → 3 targets tray → "Create slot study".
	// The URL carries the step, so a reload or a shared link lands on the same place.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import { formatApiError } from '$lib/api/core';
	import { getSlotSet } from '$lib/api/v3/slots';
	import type { ParentSelection, SlotSetDetailOut, Span } from '$lib/api/v3/types';
	import { shortSha } from '$lib/components/runs/format';
	import {
		FreezeStep,
		lineSpan,
		ParentPicker,
		selectionForSymbol,
		SlotCodePane,
		SlotFileTree,
		SlotPicker,
		SlotTargetForm,
		TargetsTray,
		type PartSelection,
	} from '$lib/components/slots';

	const setIds = $derived((page.url.searchParams.get('sets') ?? '').split(',').filter(Boolean));
	const parentIds = $derived((page.url.searchParams.get('parent') ?? '').split(',').filter(Boolean));
	const activeParam = $derived(page.url.searchParams.get('set') ?? '');
	const step = $derived(setIds.length ? 'parts' : parentIds.length ? 'freeze' : 'parents');

	// The draw/manual record of the in-page pick (a ?parent= link freezes with the server's manual default).
	let pick = $state<{ selection: ParentSelection; poolSize: number | null } | null>(null);

	function navigate(params: Record<string, string | null>) {
		const url = new URL(page.url);
		for (const [key, value] of Object.entries(params)) {
			if (value) url.searchParams.set(key, value);
			else url.searchParams.delete(key);
		}
		goto(`${url.pathname}${url.search}`, { keepFocus: true, noScroll: true });
	}

	// ── step 2: frozen sets and the picker ──────────────────────────────────
	const picker = new SlotPicker();
	let sets = $state<SlotSetDetailOut[]>([]);
	let setsLoading = $state(false);
	let setsError = $state('');

	async function loadSets(ids: string[]) {
		setsLoading = untrack(() => sets.length === 0);
		setsError = '';
		try {
			sets = await Promise.all(ids.map((id) => getSlotSet(id)));
		} catch (e) {
			setsError = formatApiError(e, 'Could not load the frozen parents.');
		} finally {
			setsLoading = false;
		}
	}

	$effect(() => {
		const ids = setIds;
		if (ids.length) void loadSets(ids);
	});

	const activeSet = $derived(setIds.includes(activeParam) ? activeParam : (setIds[0] ?? ''));

	$effect(() => {
		const id = activeSet;
		if (id) void picker.open(id);
	});

	$effect(() => () => picker.destroy());

	async function refreshAll() {
		await Promise.all([loadSets(setIds), picker.refreshSet()]);
	}

	const selectedSpan = $derived.by((): Span | null => {
		const lines = picker.selectedLines;
		const file = picker.file;
		if (!lines || !file) return null;
		return lineSpan(file.text, lines.start_line, lines.end_line);
	});

	function onCodeSelect({ span, symbolId }: { span: Span; symbolId?: string }) {
		const file = picker.file;
		if (!file) return;
		let selection: PartSelection;
		const symbol = symbolId ? file.symbols.find((s) => s.id === symbolId) : undefined;
		const component = symbolId ? picker.components.find((c) => c.symbol === symbolId) : undefined;
		if (component) {
			selection = { kind: 'component', path: component.path, component: component.name, symbol_id: component.symbol };
		} else if (symbol) {
			selection = selectionForSymbol(symbol, picker.endpoints);
		} else {
			selection = { kind: 'region', path: file.path, start_line: span.start_line, end_line: span.end_line };
		}
		picker.select(selection);
	}

	const setLabel = (s: SlotSetDetailOut) => `${s.parent_model || 'parent'} · ${shortSha(s.parent_run, 6)}`;
</script>

<div class="space-y-4">
	<PageHeader
		title={step === 'parents' ? 'Regenerate a part' : step === 'freeze' ? 'Freeze the parents' : 'Choose parts to regenerate'}
		description={step === 'parents'
			? 'Slot mode: freeze finished apps as parents, pick one part (an endpoint handler, a component, a symbol, a region or a file) and regenerate it N times while the rest stays byte-frozen.'
			: step === 'freeze'
				? 'Freezing pins each parent (tree hash, symbol index, baseline evaluation, how it was chosen); nothing is copied.'
				: 'Pick a part, check the hole, context and leak in the preview, save it as a target, then add the matching part from the other parents.'}
		back={step === 'parents' ? { href: '/applications', label: 'Applications' } : undefined}
	>
		{#snippet actions()}
			{#if step === 'parts'}
				<Button variant="outline" size="sm" href="/slots/new"><Plus class="mr-1.5 h-3.5 w-3.5" />Other parents</Button>
			{/if}
		{/snippet}
		<div class="header-stats">
			<span class="header-stat"><b class={step === 'parents' ? 'text-primary' : ''}>0</b> choose parents</span>
			<span class="header-stat"><b class={step === 'freeze' ? 'text-primary' : ''}>1</b> freeze</span>
			<span class="header-stat"><b class={step === 'parts' ? 'text-primary' : ''}>2</b> choose parts</span>
			<span class="header-stat"><b>3</b> targets → experiment</span>
		</div>
	</PageHeader>

	{#if step === 'parents'}
		<ParentPicker
			onconfirm={(p) => {
				pick = { selection: p.selection, poolSize: p.poolSize };
				navigate({ parent: p.runIds.join(','), sets: null, set: null });
			}}
		/>
	{:else if step === 'freeze'}
		<FreezeStep
			parents={parentIds}
			selection={pick?.selection ?? null}
			poolSize={pick?.poolSize ?? null}
			onback={() => navigate({ parent: null })}
			onfrozen={(ids) => navigate({ sets: ids.join(','), set: ids[0], parent: null })}
		/>
	{:else if setsLoading}
		<LoadingState label="loading frozen parents" />
	{:else if setsError}
		<ErrorAlert message={setsError} onretry={() => loadSets(setIds)} />
	{:else if sets.length === 0}
		<EmptyState title="No frozen parent" description="Freeze a parent first." />
	{:else}
		{#if sets.length > 1}
			{#if sets.length <= 4}
				<Segmented
					aria-label="Frozen parent"
					value={activeSet}
					onValueChange={(v) => navigate({ set: v })}
					items={sets.map((s) => ({ value: s.id, label: setLabel(s), badge: String(s.targets.length), title: s.name }))}
				/>
			{:else}
				<Select.Simple
					aria-label="Frozen parent"
					value={activeSet}
					onValueChange={(v) => navigate({ set: v })}
					items={sets.map((s) => ({ value: s.id, label: setLabel(s), description: `${s.name} · ${s.targets.length} targets` }))}
				/>
			{/if}
		{/if}

		{#if picker.error}<ErrorAlert message={picker.error} onretry={() => activeSet && picker.open(activeSet)} />{/if}
		{#if picker.loading && !picker.set}
			<LoadingState label="loading the parent's index" />
		{:else if picker.set}
			<p class="text-xs text-muted-foreground">
				{picker.set.name || 'Slot set'} · task <span class="font-mono">{picker.set.task ?? '—'}</span> · parent
				<a class="font-mono text-primary hover:underline" href={`/samples/${picker.set.parent_run}`}>{shortSha(picker.set.parent_run, 8)}</a>
				({picker.set.parent_model} · {picker.set.parent_harness}) · {picker.set.index_stats.files} files ·
				{picker.set.index_stats.symbols} symbols · {picker.set.index_stats.endpoints} endpoints · {picker.set.index_stats.components} components
				{#if picker.set.index_stats.unparsed.length}· {picker.set.index_stats.unparsed.length} unparsed{/if}
				· <a class="text-primary hover:underline" href={`/slots/${picker.set.id}`}>slot set page</a>
			</p>
			<div class="grid gap-4 xl:grid-cols-[17rem_minmax(0,1fr)_25rem]">
				<Card.Root class="gap-0 py-0 xl:max-h-[calc(100vh-12rem)]">
					<Card.Content class="flex h-full min-h-[16rem] flex-col p-2">
						<SlotFileTree {picker} />
					</Card.Content>
				</Card.Root>
				<Card.Root class="gap-0 py-0 xl:max-h-[calc(100vh-12rem)]">
					<Card.Content class="flex h-full min-h-[20rem] flex-col p-3">
						{#if picker.path}
							<SlotCodePane setId={picker.set.id} path={picker.path} file={picker.file} selection={selectedSpan} onselect={onCodeSelect} />
						{:else}
							<EmptyState compact title="Open a file" description="Pick a file on the left." />
						{/if}
					</Card.Content>
				</Card.Root>
				<Card.Root class="gap-0 py-0 xl:max-h-[calc(100vh-12rem)] xl:overflow-auto">
					<Card.Content class="p-3">
						<SlotTargetForm {picker} onsaved={() => void loadSets(setIds)} />
					</Card.Content>
				</Card.Root>
			</div>
		{/if}

		<TargetsTray {sets} onchanged={refreshAll} />
	{/if}
</div>
