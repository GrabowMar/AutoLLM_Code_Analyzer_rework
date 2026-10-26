<script lang="ts" module>
	import type { Severity } from '$lib/api/analysis';

	/** A gutter marker: findings on one line. */
	export interface LineMarker {
		severity: Severity;
		count: number;
		/** Tooltip / accessible label, e.g. "2 findings: B602, S602". */
		label: string;
	}
</script>

<script lang="ts">
	/**
	 * CodeViewer — read-only source with Shiki highlighting (shared
	 * highlighter in `$lib/utils/highlight`), line numbers, a highlighted
	 * range, optional context window and per-line markers.
	 *
	 * Props:
	 * - `code`: the text; `path` (header + language detection), `language`
	 *   (the manifest's language name; wins over the extension).
	 * - `startLine` (default 1): number of the first line of `code` (snippets).
	 * - `highlight`: `{ start, end? }` 1-based inclusive range, scrolled into view.
	 * - `context`: show only this many lines around `highlight` (null: whole
	 *   file); a toggle reveals the whole file.
	 * - `markers`: `{ [line]: LineMarker }` gutter dots (severity-colored);
	 *   `changedLines`: lines the model changed in a modified file.
	 * - `onlineclick(line)`: makes numbered lines clickable (e.g. select findings).
	 * - `maxHeight` (Tailwind class, default `max-h-[28rem]`), `wrap`, `class`.
	 * - `header` (default true): file name, language, line count and Copy.
	 *
	 * The first paint is plain text; highlighted tokens replace it when Shiki
	 * is ready (a failure keeps the plain text).
	 */
	import { tick } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { cn } from '$lib/utils';
	import {
		highlightLines,
		languageForFile,
		plainLines,
		type HighlightedLine
	} from '$lib/utils/highlight';
	import Copy from '@lucide/svelte/icons/copy';
	import UnfoldVertical from '@lucide/svelte/icons/unfold-vertical';
	import FoldVertical from '@lucide/svelte/icons/fold-vertical';

	interface Props {
		code: string;
		path?: string;
		language?: string | null;
		startLine?: number;
		highlight?: { start: number; end?: number | null } | null;
		context?: number | null;
		markers?: Record<number, LineMarker>;
		changedLines?: number[] | null;
		onlineclick?: (line: number) => void;
		maxHeight?: string;
		wrap?: boolean;
		header?: boolean;
		class?: string;
	}

	let {
		code,
		path = '',
		language = null,
		startLine = 1,
		highlight = null,
		context = null,
		markers = {},
		changedLines = null,
		onlineclick,
		maxHeight = 'max-h-[28rem]',
		wrap = false,
		header = true,
		class: className
	}: Props = $props();

	const lang = $derived(languageForFile(path, language));

	// A trailing newline is not a line of its own in an editor.
	const source = $derived(code.endsWith('\n') ? code.slice(0, -1) : code);

	let tokens = $state<HighlightedLine[]>([]);
	let seq = 0;

	$effect(() => {
		const text = source;
		const id = lang;
		const mine = ++seq;
		tokens = plainLines(text);
		highlightLines(text, id)
			.then((lines) => {
				if (mine === seq) tokens = lines;
			})
			.catch(() => {
				// Keep the plain rendering: highlighting is cosmetic.
			});
	});

	const lineCount = $derived(tokens.length);
	const hlStart = $derived(highlight ? highlight.start : null);
	const hlEnd = $derived(
		highlight ? Math.max(highlight.start, highlight.end ?? highlight.start) : null
	);

	let showAll = $state(false);
	const windowed = $derived(context != null && hlStart != null && !showAll && lineCount > 0);
	const firstVisible = $derived(
		windowed && hlStart != null ? Math.max(startLine, hlStart - (context ?? 0)) : startLine
	);
	const lastVisible = $derived(
		windowed && hlEnd != null
			? Math.min(startLine + lineCount - 1, hlEnd + (context ?? 0))
			: startLine + lineCount - 1
	);

	const changed = $derived(new Set(changedLines ?? []));

	const rows = $derived(
		tokens
			.map((line, i) => ({ n: startLine + i, line }))
			.filter((row) => row.n >= firstVisible && row.n <= lastVisible)
	);

	const gutterWidth = $derived(`${String(lastVisible).length + 1}ch`);

	let scroller = $state<HTMLDivElement | null>(null);

	// Center the highlighted range whenever it (or the rendered rows) change.
	$effect(() => {
		const start = hlStart;
		void rows.length;
		if (start == null || !scroller) return;
		tick().then(() => {
			const el = scroller?.querySelector<HTMLElement>(`[data-line="${start}"]`);
			if (!el || !scroller) return;
			scroller.scrollTop = Math.max(0, el.offsetTop - scroller.clientHeight / 2 + el.offsetHeight);
		});
	});

	function isHighlighted(n: number): boolean {
		return hlStart != null && hlEnd != null && n >= hlStart && n <= hlEnd;
	}

	async function copyAll() {
		try {
			await navigator.clipboard.writeText(code);
			toast.success('Copied to clipboard');
		} catch {
			toast.error('Could not copy');
		}
	}

	const fileName = $derived(path ? path.split('/').pop() : '');
</script>

<div class={cn('overflow-hidden rounded-md border border-border bg-surface-2', className)}>
	{#if header}
		<div
			class="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border bg-muted/40 px-3 py-1.5 text-xs"
		>
			{#if path}
				<span class="min-w-0 truncate font-mono" title={path}>{fileName}</span>
			{/if}
			<span class="text-muted-foreground">{lang}</span>
			<span class="text-muted-foreground tabular-nums">
				{lineCount.toLocaleString()} line{lineCount === 1 ? '' : 's'}
			</span>
			<span class="ml-auto flex items-center gap-1">
				{#if context != null && hlStart != null}
					<button
						type="button"
						class="inline-flex h-6 cursor-pointer items-center gap-1 rounded px-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
						onclick={() => (showAll = !showAll)}
						aria-pressed={showAll}
					>
						{#if showAll}
							<FoldVertical class="size-3.5" aria-hidden="true" />
							Context only
						{:else}
							<UnfoldVertical class="size-3.5" aria-hidden="true" />
							Whole file
						{/if}
					</button>
				{/if}
				<button
					type="button"
					class="inline-flex h-6 cursor-pointer items-center gap-1 rounded px-1.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
					onclick={copyAll}
					aria-label="Copy {path ? fileName : 'code'}"
				>
					<Copy class="size-3.5" aria-hidden="true" />
					<span class="hidden sm:inline">Copy</span>
				</button>
			</span>
		</div>
	{/if}

	<div
		bind:this={scroller}
		class={cn('cv-scroll relative overflow-auto', maxHeight)}
		role="region"
		aria-label={path ? `Source of ${path}` : 'Source code'}
		tabindex="-1"
	>
		{#if windowed && firstVisible > startLine}
			<div class="cv-fold" aria-hidden="true">
				⋯ {firstVisible - startLine} line{firstVisible - startLine === 1 ? '' : 's'} above
			</div>
		{/if}
		<div class="cv-code font-mono" class:cv-wrap={wrap}>
			{#each rows as row (row.n)}
				{@const marker = markers[row.n]}
				<div class="cv-line" class:cv-hl={isHighlighted(row.n)} data-line={row.n}>
					<span class="cv-gutter" style:width={gutterWidth} class:cv-changed={changed.has(row.n)}>
						{#if onlineclick}
							<button
								type="button"
								class="cv-num cursor-pointer hover:text-foreground"
								onclick={() => onlineclick(row.n)}
								aria-label="Line {row.n}{marker ? `, ${marker.label}` : ''}">{row.n}</button
							>
						{:else}
							<span class="cv-num" aria-hidden="true">{row.n}</span>
						{/if}
					</span>
					<span class="cv-mark" aria-hidden={marker ? undefined : 'true'}>
						{#if marker}
							<span
								class="cv-dot cv-sev-{marker.severity}"
								title={marker.label}
								role="img"
								aria-label={marker.label}
							></span>
						{/if}
					</span>
					<span class="cv-text"
						>{#each row.line as token, i (i)}<span style={token.style}>{token.content}</span
							>{/each}{#if row.line.length === 0}&#8203;{/if}</span
					>
				</div>
			{/each}
		</div>
		{#if windowed && lastVisible < startLine + lineCount - 1}
			<div class="cv-fold" aria-hidden="true">
				⋯ {startLine + lineCount - 1 - lastVisible} line{startLine + lineCount - 1 - lastVisible ===
				1
					? ''
					: 's'} below
			</div>
		{/if}
	</div>
</div>

<style>
	.cv-code {
		min-width: max-content;
		padding: 0.5rem 0;
		font-size: 0.78rem;
		line-height: 1.6;
		tab-size: 4;
		color: var(--foreground);
	}
	.cv-code.cv-wrap {
		min-width: 0;
	}
	.cv-line {
		display: flex;
		align-items: flex-start;
	}
	.cv-hl {
		background: color-mix(in oklab, var(--primary) 12%, transparent);
		box-shadow: inset 2px 0 0 var(--primary);
	}
	.cv-gutter {
		position: sticky;
		left: 0;
		z-index: 1;
		flex-shrink: 0;
		padding: 0 0.5rem 0 0.25rem;
		text-align: right;
		color: color-mix(in oklab, var(--muted-foreground) 75%, transparent);
		background: var(--surface-2);
		border-right: 1px solid color-mix(in oklab, var(--border) 70%, transparent);
		font-variant-numeric: tabular-nums;
		user-select: none;
		box-sizing: content-box;
	}
	.cv-hl .cv-gutter {
		color: var(--foreground);
		background: color-mix(in oklab, var(--primary) 12%, var(--surface-2));
	}
	.cv-gutter.cv-changed {
		box-shadow: inset -2px 0 0 var(--info);
	}
	.cv-num {
		font: inherit;
		color: inherit;
		background: none;
		border: 0;
		padding: 0;
	}
	.cv-mark {
		display: inline-flex;
		width: 1rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		height: 1.6em;
	}
	.cv-dot {
		width: 0.45rem;
		height: 0.45rem;
		border-radius: 9999px;
	}
	.cv-sev-critical {
		background: var(--destructive);
		box-shadow: 0 0 0 1.5px color-mix(in oklab, var(--destructive) 35%, transparent);
	}
	.cv-sev-high {
		background: var(--destructive);
	}
	.cv-sev-medium {
		background: var(--warning);
	}
	.cv-sev-low {
		background: var(--info);
	}
	.cv-sev-info {
		background: var(--muted-foreground);
	}
	.cv-text {
		white-space: pre;
		padding-right: 1rem;
	}
	.cv-wrap .cv-text {
		white-space: pre-wrap;
		word-break: break-word;
	}
	.cv-text span {
		color: var(--shiki-light);
	}
	:global(.dark) .cv-text span {
		color: var(--shiki-dark);
	}
	.cv-fold {
		padding: 0.15rem 0.75rem;
		font-size: 0.7rem;
		color: var(--muted-foreground);
		background: color-mix(in oklab, var(--muted) 50%, transparent);
	}
</style>
