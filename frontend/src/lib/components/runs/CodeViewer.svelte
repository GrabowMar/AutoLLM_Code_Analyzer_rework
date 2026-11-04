<script lang="ts">
	/**
	 * CodeViewer — shiki-highlighted source with a line gutter (sample files, slot picker, diffs).
	 *
	 * Props:
	 * - `code`, `language` (server language), `path` (for the extension fallback);
	 * - `highlight` `{start_line, end_line}` marks a range (the current selection or a part);
	 * - `selectable`: dragging over the gutter selects whole lines → `onselectlines(start, end, done)`;
	 *   `onlineclick(line)` fires for a click on a code line (the slot pane snaps it to a symbol);
	 * - `markers`: line → outline chips (symbols starting there) → `onmarker(id)`; `activeMarker` is pressed;
	 * - `scrollToLine` scrolls that line into view when it changes; `class` sizes the scroll box.
	 *
	 * Keyboard users select regions with the line fields of the target form; outline chips are buttons.
	 */
	import { cn } from '$lib/utils';
	import { plainLines, shikiLang, tokenize, type LineMarker, type TokenLine } from './highlight';

	interface Props {
		code: string;
		language?: string | null;
		path?: string;
		highlight?: { start_line: number; end_line: number } | null;
		selectable?: boolean;
		markers?: Map<number, LineMarker[]>;
		activeMarker?: string | null;
		scrollToLine?: number | null;
		onselectlines?: (start: number, end: number, done: boolean) => void;
		onlineclick?: (line: number) => void;
		onmarker?: (id: string) => void;
		class?: string;
		'aria-label'?: string;
	}

	let {
		code,
		language = null,
		path,
		highlight = null,
		selectable = false,
		markers,
		activeMarker = null,
		scrollToLine = null,
		onselectlines,
		onlineclick,
		onmarker,
		class: className,
		'aria-label': ariaLabel,
	}: Props = $props();

	let lines = $state<TokenLine[]>([]);
	let container = $state<HTMLDivElement | null>(null);
	let dragFrom = $state<number | null>(null);
	let dragTo = $state<number | null>(null);

	$effect(() => {
		const text = code;
		const lang = shikiLang(language, path);
		lines = plainLines(text);
		let cancelled = false;
		tokenize(text, lang).then((tokens) => {
			if (!cancelled) lines = tokens;
		});
		return () => {
			cancelled = true;
		};
	});

	const range = $derived.by(() => {
		if (dragFrom !== null && dragTo !== null) {
			return { start: Math.min(dragFrom, dragTo), end: Math.max(dragFrom, dragTo) };
		}
		return highlight ? { start: highlight.start_line, end: highlight.end_line } : null;
	});

	$effect(() => {
		const line = scrollToLine;
		if (!line || !container) return;
		const row = container.querySelector<HTMLElement>(`[data-line="${line}"]`);
		if (!row) return;
		const top = row.offsetTop - container.clientHeight / 3;
		container.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
	});

	function startDrag(line: number, event: PointerEvent) {
		if (!selectable || event.button !== 0) return;
		event.preventDefault();
		dragFrom = line;
		dragTo = line;
		onselectlines?.(line, line, false);
		window.addEventListener('pointerup', endDrag, { once: true });
	}

	function extendDrag(line: number) {
		if (dragFrom === null) return;
		if (dragTo === line) return;
		dragTo = line;
		onselectlines?.(Math.min(dragFrom, line), Math.max(dragFrom, line), false);
	}

	function endDrag() {
		if (dragFrom !== null && dragTo !== null) {
			onselectlines?.(Math.min(dragFrom, dragTo), Math.max(dragFrom, dragTo), true);
		}
		dragFrom = null;
		dragTo = null;
	}

	function inRange(line: number): boolean {
		return !!range && range.start <= line && line <= range.end;
	}
</script>

<div
	bind:this={container}
	class={cn('code-viewer overflow-auto rounded-md border border-border bg-[color:var(--surface-2,var(--muted))]', className)}
	role="region"
	aria-label={ariaLabel ?? (path ? `Source of ${path}` : 'Source')}
>
	<table class="code-table w-full border-collapse font-mono text-xs leading-5">
		<tbody>
			{#each lines as line, i (i)}
				{@const n = i + 1}
				{@const lineMarkers = markers?.get(n)}
				<tr
					data-line={n}
					class={cn('code-row', inRange(n) && 'code-row-selected')}
					onpointerenter={() => extendDrag(n)}
				>
					<td
						class={cn(
							'code-gutter select-none text-right tabular-nums text-muted-foreground',
							selectable && 'cursor-pointer hover:text-foreground',
						)}
						onpointerdown={(e) => startDrag(n, e)}
						title={selectable ? 'Drag over line numbers to select a region' : undefined}
					>
						{n}
					</td>
					{#if markers}
						<td class="code-markers select-none">
							{#if lineMarkers}
								{#each lineMarkers as marker (marker.id)}
									<button
										type="button"
										class={cn(
											'code-marker cursor-pointer rounded border px-1 text-[11px] leading-4 transition-colors',
											activeMarker === marker.id
												? 'border-primary bg-primary/10 text-primary'
												: 'border-border text-muted-foreground hover:border-primary/60 hover:text-foreground',
										)}
										title={marker.title ?? marker.label}
										aria-pressed={activeMarker === marker.id}
										onclick={() => onmarker?.(marker.id)}
									>
										{marker.label}
									</button>
								{/each}
							{/if}
						</td>
					{/if}
					<td
						class={cn('code-text whitespace-pre', onlineclick && 'cursor-text')}
						onclick={() => {
							if (!window.getSelection()?.toString()) onlineclick?.(n);
						}}
					>{#each line.tokens as token, t (t)}<span class="code-tok" style={token.style}>{token.content}</span>{/each}{#if line.tokens.length === 0 || (line.tokens.length === 1 && line.tokens[0].content === '')}&#8203;{/if}</td>
				</tr>
			{/each}
		</tbody>
	</table>
</div>

<style>
	.code-table td {
		padding: 0;
		border: 0;
		vertical-align: top;
	}
	.code-gutter {
		width: 1%;
		padding: 0 0.6rem 0 0.75rem !important;
		position: sticky;
		left: 0;
		background: inherit;
	}
	.code-markers {
		width: 1%;
		padding: 0 0.4rem 0 0 !important;
		white-space: nowrap;
	}
	.code-text {
		padding: 0 0.75rem 0 0 !important;
	}
	.code-row-selected {
		background: color-mix(in oklab, var(--primary) 12%, transparent);
	}
	.code-row-selected .code-gutter {
		color: var(--primary);
		font-weight: 600;
	}
	.code-tok {
		color: var(--shiki-light, inherit);
	}
	:global(.dark) .code-tok {
		color: var(--shiki-dark, inherit);
	}
</style>
