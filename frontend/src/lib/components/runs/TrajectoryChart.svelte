<script lang="ts">
	/**
	 * TrajectoryChart — how a run's context grew, turn by turn: prompt tokens of each LLM call as an area
	 * (the context the model saw), its output tokens as bars on their own scale, and turns whose tool calls
	 * failed marked along the top. Hover / focus a turn for its numbers; click selects it (`onselect`).
	 */
	import type { TrajectoryTurn } from './format';
	import { fmtInt, turnTools } from './format';

	interface Props {
		turns: TrajectoryTurn[];
		selected?: number | null;
		onselect?: (n: number) => void;
	}

	let { turns, selected = null, onselect }: Props = $props();

	const W = 1000;
	const H = 180;
	const PAD = { l: 44, r: 12, t: 16, b: 22 };
	const iw = W - PAD.l - PAD.r;
	const ih = H - PAD.t - PAD.b;

	const llmTurns = $derived(turns.filter((t) => t.llm));
	const maxIn = $derived(Math.max(1, ...llmTurns.map((t) => t.tokensIn)));
	const maxOut = $derived(Math.max(1, ...llmTurns.map((t) => t.tokensOut)));
	const step = $derived(llmTurns.length > 1 ? iw / (llmTurns.length - 1) : iw);
	const barW = $derived(Math.max(2, Math.min(14, step * 0.55)));

	const x = (i: number) => PAD.l + (llmTurns.length > 1 ? i * step : iw / 2);
	const yIn = (v: number) => PAD.t + ih - (v / maxIn) * ih;
	const yOut = (v: number) => PAD.t + ih - (v / maxOut) * ih * 0.45;

	const area = $derived.by(() => {
		if (!llmTurns.length) return { line: '', fill: '' };
		const pts = llmTurns.map((t, i) => `${x(i).toFixed(1)},${yIn(t.tokensIn).toFixed(1)}`);
		const line = `M${pts.join(' L')}`;
		const fill = `${line} L${x(llmTurns.length - 1).toFixed(1)},${PAD.t + ih} L${x(0).toFixed(1)},${PAD.t + ih} Z`;
		return { line, fill };
	});

	const ticks = $derived([0, 0.5, 1].map((f) => ({ y: PAD.t + ih - f * ih, label: compact(maxIn * f) })));
	function compact(n: number): string {
		return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : String(Math.round(n));
	}

	let hover = $state<number | null>(null);
	const active = $derived(hover ?? selected);
	const activeIdx = $derived(active == null ? -1 : llmTurns.findIndex((t) => t.n === active));
	const activeTurn = $derived(activeIdx >= 0 ? llmTurns[activeIdx] : null);

	function pick(e: PointerEvent) {
		const svg = e.currentTarget as SVGSVGElement;
		const r = svg.getBoundingClientRect();
		const px = ((e.clientX - r.left) / r.width) * W;
		const i = Math.round((px - PAD.l) / (step || 1));
		const clamped = Math.max(0, Math.min(llmTurns.length - 1, i));
		hover = llmTurns[clamped]?.n ?? null;
	}

	function onKey(e: KeyboardEvent) {
		if (!llmTurns.length) return;
		const cur = activeIdx < 0 ? 0 : activeIdx;
		if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
			e.preventDefault();
			const next = Math.max(0, Math.min(llmTurns.length - 1, cur + (e.key === 'ArrowRight' ? 1 : -1)));
			hover = llmTurns[next].n;
		} else if (e.key === 'Enter' && activeTurn) {
			onselect?.(activeTurn.n);
		}
	}
</script>

{#if llmTurns.length > 1}
	<figure class="tc">
		<div class="tc-legend">
			<span><i class="tc-sw tc-sw-in"></i>Context (prompt tokens)</span>
			<span><i class="tc-sw tc-sw-out"></i>Output tokens <span class="text-muted-foreground">(own scale)</span></span>
			<span><i class="tc-sw tc-sw-fail"></i>Failed tool call</span>
			<span class="tc-readout" aria-live="polite">
				{#if activeTurn}
					<b>Turn {activeTurn.n}</b>{` · ${fmtInt(activeTurn.tokensIn)} in · ${fmtInt(activeTurn.tokensOut)} out`}{#if activeTurn.actions.length}{` · ${turnTools(activeTurn)}`}{/if}{#if activeTurn.failed}{' · '}<span class="text-danger-fg">{activeTurn.failed} failed</span>{/if}
				{:else}
					<span class="text-muted-foreground">Hover or use ← → to inspect a turn</span>
				{/if}
			</span>
		</div>
		<!-- A turn picker: arrows move between turns, Enter jumps to the turn in the list below. -->
		<svg
			viewBox="0 0 {W} {H}"
			class="tc-svg"
			role="slider"
			tabindex="0"
			aria-valuemin={llmTurns[0].n}
			aria-valuemax={llmTurns.at(-1)?.n ?? 1}
			aria-valuenow={activeTurn?.n ?? llmTurns[0].n}
			aria-valuetext={activeTurn ? `Turn ${activeTurn.n}: ${fmtInt(activeTurn.tokensIn)} prompt tokens` : undefined}
			aria-label="Context grew from {fmtInt(llmTurns[0].tokensIn)} to {fmtInt(llmTurns.at(-1)?.tokensIn)} prompt tokens over {llmTurns.length} turns"
			onpointermove={pick}
			onpointerleave={() => (hover = null)}
			onclick={() => activeTurn && onselect?.(activeTurn.n)}
			onkeydown={onKey}
		>
			{#each ticks as t (t.y)}
				<line x1={PAD.l} x2={W - PAD.r} y1={t.y} y2={t.y} class="tc-grid" />
				<text x={PAD.l - 6} y={t.y + 3} class="tc-tick" text-anchor="end">{t.label}</text>
			{/each}
			{#each llmTurns as t, i (t.n)}
				<rect x={x(i) - barW / 2} y={yOut(t.tokensOut)} width={barW} height={PAD.t + ih - yOut(t.tokensOut)} class="tc-bar" />
			{/each}
			<path d={area.fill} class="tc-area" />
			<path d={area.line} class="tc-line" vector-effect="non-scaling-stroke" />
			{#each llmTurns as t, i (t.n)}
				{#if t.failed}<rect x={x(i) - 2} y={PAD.t - 12} width="4" height="8" rx="1" class="tc-fail" />{/if}
			{/each}
			{#if activeIdx >= 0}
				<line x1={x(activeIdx)} x2={x(activeIdx)} y1={PAD.t - 4} y2={PAD.t + ih} class="tc-cursor" vector-effect="non-scaling-stroke" />
				<circle cx={x(activeIdx)} cy={yIn(llmTurns[activeIdx].tokensIn)} r="4" class="tc-dot" />
			{/if}
			<text x={PAD.l} y={H - 6} class="tc-tick">turn 1</text>
			<text x={W - PAD.r} y={H - 6} class="tc-tick" text-anchor="end">turn {llmTurns.at(-1)?.n}</text>
		</svg>
	</figure>
{/if}

<style>
	.tc {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
	}
	.tc-legend {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.25rem 1rem;
		font-size: 0.75rem;
	}
	.tc-legend > span {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
	}
	.tc-readout {
		margin-left: auto;
		font-variant-numeric: tabular-nums;
	}
	.tc-sw {
		display: inline-block;
		width: 0.625rem;
		height: 0.625rem;
		border-radius: 2px;
	}
	.tc-sw-in {
		background: color-mix(in oklab, var(--primary) 45%, transparent);
		border-top: 2px solid var(--primary);
	}
	.tc-sw-out {
		background: color-mix(in oklab, var(--chart-3, #c2850c) 70%, transparent);
	}
	.tc-sw-fail {
		background: var(--destructive);
	}
	.tc-svg {
		width: 100%;
		height: auto;
		cursor: crosshair;
		border-radius: 0.25rem;
	}
	.tc-svg:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 2px;
	}
	.tc-grid {
		stroke: var(--border);
		stroke-width: 1;
		vector-effect: non-scaling-stroke;
	}
	.tc-tick {
		font-family: var(--font-mono);
		font-size: 10px;
		fill: var(--muted-foreground);
	}
	.tc-area {
		fill: color-mix(in oklab, var(--primary) 16%, transparent);
	}
	.tc-line {
		fill: none;
		stroke: var(--primary);
		stroke-width: 1.75;
	}
	.tc-bar {
		fill: color-mix(in oklab, var(--chart-3, #c2850c) 55%, transparent);
	}
	.tc-fail {
		fill: var(--destructive);
	}
	.tc-cursor {
		stroke: var(--foreground);
		stroke-opacity: 0.35;
		stroke-dasharray: 3 3;
	}
	.tc-dot {
		fill: var(--card);
		stroke: var(--primary);
		stroke-width: 2;
		vector-effect: non-scaling-stroke;
	}
</style>
