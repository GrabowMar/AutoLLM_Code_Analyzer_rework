<script lang="ts">
	/**
	 * SlotCodePane — the centre pane of the slot picker (spec 10.5 step 2): one file of the frozen parent,
	 * highlighted, with a selectable line gutter and symbol outlines. Clicking an outline chip selects that
	 * symbol's span; dragging over the gutter selects a region, snapped to whole lines and (with "Snap to
	 * symbols", on by default) widened so it never cuts a function in half.
	 *
	 * Props (spec 10.8 contract): `setId`, `path`, `selection` (the selected span, highlighted),
	 * `onselect({span, symbolId?})`. Optional `file` (an already loaded `SlotFileOut` for `path`, so the picker
	 * store's cache is reused instead of refetching).
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSlotSetFile } from '$lib/api/v3/slots';
	import type { SlotFileOut, Span } from '$lib/api/v3/types';
	import CodeViewer from '$lib/components/runs/CodeViewer.svelte';
	import type { LineMarker } from '$lib/components/runs/highlight';
	import { lineSpan, selectableSymbols, snapLinesToSymbols } from './selection';

	interface Props {
		setId: string;
		path: string;
		selection: Span | null;
		onselect: (s: { span: Span; symbolId?: string }) => void;
		file?: SlotFileOut | null;
	}

	let { setId, path, selection, onselect, file = null }: Props = $props();

	let loaded = $state<SlotFileOut | null>(null);
	let loading = $state(false);
	let error = $state('');
	let snap = $state(true);
	let dragging = $state<{ start: number; end: number } | null>(null);

	const current = $derived(file && file.path === path ? file : loaded && loaded.path === path ? loaded : null);

	$effect(() => {
		if (file && file.path === path) return;
		const controller = new AbortController();
		loading = true;
		error = '';
		getSlotSetFile(setId, path, { signal: controller.signal })
			.then((out) => (loaded = out))
			.catch((e) => {
				if (!isAbortError(e)) error = formatApiError(e, 'Could not load the file.');
			})
			.finally(() => (loading = false));
		return () => controller.abort();
	});

	const symbols = $derived(current ? selectableSymbols(current.symbols) : []);
	const markers = $derived.by(() => {
		const map = new Map<number, LineMarker[]>();
		for (const s of symbols) {
			const list = map.get(s.span.start_line) ?? [];
			list.push({
				id: s.id,
				label: s.qualname.split('.').pop() ?? s.qualname,
				title: `${s.kind} ${s.qualname} (lines ${s.span.start_line}-${s.span.end_line})${s.signature ? `\n${s.signature}` : ''}`,
			});
			map.set(s.span.start_line, list);
		}
		return map;
	});
	const activeSymbol = $derived(
		selection ? (symbols.find((s) => s.span.start_line === selection.start_line && s.span.end_line === selection.end_line)?.id ?? null) : null,
	);
	const highlight = $derived(
		dragging
			? { start_line: dragging.start, end_line: dragging.end }
			: selection
				? { start_line: selection.start_line, end_line: selection.end_line }
				: null,
	);

	function onlines(start: number, end: number, done: boolean) {
		if (!current) return;
		if (!done) {
			dragging = { start, end };
			return;
		}
		dragging = null;
		if (snap) {
			const snapped = snapLinesToSymbols(current.symbols, start, end);
			const span = lineSpan(current.text, snapped.start_line, snapped.end_line);
			onselect(snapped.symbol ? { span, symbolId: snapped.symbol.id } : { span });
		} else {
			onselect({ span: lineSpan(current.text, start, end) });
		}
	}

	function onmarker(id: string) {
		const symbol = symbols.find((s) => s.id === id);
		if (symbol) onselect({ span: symbol.span, symbolId: symbol.id });
	}
</script>

<div class="flex h-full min-h-0 flex-col gap-2">
	<div class="flex flex-wrap items-center gap-3 text-xs">
		<span class="min-w-0 truncate font-mono font-medium">{path}</span>
		{#if current}<span class="text-muted-foreground">{current.language} · {symbols.length} symbols</span>{/if}
		<label class="ml-auto flex items-center gap-1.5" title="Widen a dragged region so it never cuts a symbol in half">
			<Checkbox bind:checked={snap} />Snap to symbols
		</label>
	</div>
	{#if loading && !current}
		<LoadingState label="loading file" />
	{:else if error}
		<ErrorAlert message={error} />
	{:else if current}
		<CodeViewer
			code={current.text}
			language={current.language}
			{path}
			selectable
			{markers}
			activeMarker={activeSymbol}
			{highlight}
			scrollToLine={selection?.start_line ?? null}
			onselectlines={onlines}
			{onmarker}
			class="min-h-[20rem] flex-1"
			aria-label={`Source of ${path}: drag over line numbers to select a region, or pick a symbol chip`}
		/>
		<p class="text-[11px] text-muted-foreground">
			Pick a symbol chip to select a function, class or component; drag over line numbers to select a region.
		</p>
	{/if}
</div>
