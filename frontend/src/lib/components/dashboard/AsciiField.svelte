<script lang="ts">
	/**
	 * AsciiField — decorative, interactive ASCII backdrop for the dashboard masthead.
	 *
	 * A drifting interference field drawn with a density ramp (" .·:-=+*#"), faded out on the left so
	 * the headline stays on clean paper. Interaction is read from the PARENT element (the canvas itself
	 * is pointer-events: none, so buttons and links underneath keep working):
	 *   - pointer = a "scanner lens": cells near it light up in the primary color and turn into code glyphs;
	 *   - click/tap = a ripple ring;
	 *   - idle = a slow scan column sweeps across so the effect reads as alive without input.
	 * Cost control: ~24 fps cap, one canvas, cells bucketed by alpha (few fillStyle changes), paused while
	 * off-screen or the tab is hidden; prefers-reduced-motion draws a static frame and redraws only on input.
	 */
	import { onMount } from 'svelte';

	interface Props {
		/** Fraction of the width (0–1) where the field starts fading in; null covers the whole host. */
		fadeFrom?: number | null;
		class?: string;
	}
	let { fadeFrom = 0.3, class: className = '' }: Props = $props();

	let canvas: HTMLCanvasElement;

	const RAMP = ' .·:-=+*#';
	const CODE = '{}[]()<>/=;:01$#_*&|!?';
	const FONT_PX = 12;
	const FRAME_MS = 42;
	const LEVELS = 6;

	onMount(() => {
		const ctx = canvas.getContext('2d');
		const host = canvas.parentElement;
		if (!ctx || !host) return;

		const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
		let w = 0, h = 0, cols = 0, rows = 0, cw = 7.2, ch = 14;
		let ink = '#e4e8f0', accent = '#7b91ff', warm = '#c2850c';
		let pointer: { x: number; y: number } | null = null;
		const ripples: { x: number; y: number; t0: number }[] = [];
		let raf = 0, last = 0, visible = true;
		const t0 = performance.now();
		// Stable per-cell glyph pick so code glyphs flicker slowly instead of every frame.
		let seeds = new Float32Array(0);

		function readColors() {
			const cs = getComputedStyle(host!);
			ink = cs.getPropertyValue('--foreground').trim() || ink;
			accent = cs.getPropertyValue('--primary').trim() || accent;
			warm = cs.getPropertyValue('--chart-3').trim() || warm;
		}

		function resize() {
			const r = host!.getBoundingClientRect();
			const dpr = Math.min(window.devicePixelRatio || 1, 2);
			w = r.width; h = r.height;
			canvas.width = Math.round(w * dpr);
			canvas.height = Math.round(h * dpr);
			canvas.style.width = `${w}px`;
			canvas.style.height = `${h}px`;
			ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
			ctx!.font = `${FONT_PX}px "IBM Plex Mono", ui-monospace, monospace`;
			ctx!.textBaseline = 'top';
			cw = ctx!.measureText('M').width || 7.2;
			ch = FONT_PX + 3;
			cols = Math.ceil(w / cw);
			rows = Math.ceil(h / ch);
			seeds = new Float32Array(cols * rows);
			for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random();
		}

		// Buckets reused across frames: [level][x,y,charIndex...]
		const ambient: string[][] = Array.from({ length: LEVELS }, () => []);
		const ambientXY: number[][] = Array.from({ length: LEVELS }, () => []);
		const hot: string[] = [];
		const hotXY: number[] = [];
		const hotA: number[] = [];

		function draw(now: number) {
			const t = (now - t0) / 1000;
			ctx!.clearRect(0, 0, w, h);
			for (let l = 0; l < LEVELS; l++) { ambient[l].length = 0; ambientXY[l].length = 0; }
			hot.length = 0; hotXY.length = 0; hotA.length = 0;

			const px = pointer ? pointer.x / cw : -999;
			const py = pointer ? pointer.y / ch : -999;
			const lensR = 9; // cells
			// Idle scan column: sweeps every ~9 s, only when no pointer is present.
			const scanX = pointer ? -999 : ((t / 9) % 1.2) * cols * 1.0 - cols * 0.1;
			for (let i = ripples.length - 1; i >= 0; i--) if (t - ripples[i].t0 > 2.4) ripples.splice(i, 1);

			const pcx = cols * 0.82, pcy = rows * 0.45;
			const fadeStart = cols * (fadeFrom ?? 0);
			const fadeEnd = cols * Math.min((fadeFrom ?? 0) + 0.35, 1);

			for (let y = 0; y < rows; y++) {
				for (let x = 0; x < cols; x++) {
					// Left fade (keeps the headline readable) + soft top/bottom falloff.
					const fx = fadeFrom === null ? 1 : x <= fadeStart ? 0 : x >= fadeEnd ? 1 : (x - fadeStart) / (fadeEnd - fadeStart);
					const fy = Math.min(1, (y + 1) / 3, (rows - y) / 3);
					const mask = fx * fx * (3 - 2 * fx) * fy;

					// Domain-warped flow + a slow radar pulse from a point on the right.
					const wx = x * 0.07 + Math.sin(y * 0.33 + t * 0.4) * 1.4;
					const wy = y * 0.3 + Math.sin(x * 0.045 - t * 0.25) * 1.8;
					const rx = (x - pcx) * 0.5, ry = y - pcy;
					let v =
						Math.sin(wx + t * 0.5) +
						Math.sin(wy - t * 0.35) +
						Math.sin(Math.sqrt(rx * rx + ry * ry) * 0.55 - t * 1.6) * 0.8;
					v = (v + 2.8) / 5.6; // ~0..1
					v = v * v * (3 - 2 * v); // contrast

					// Lens / scan / ripple boosts.
					let boost = 0;
					const dx = (x - px) * 0.55, dy = y - py; // cells are ~2x taller than wide
					const d = Math.sqrt(dx * dx + dy * dy);
					if (d < lensR) boost = Math.max(boost, 1 - d / lensR);
					const ds = Math.abs(x - scanX);
					if (ds < 3) boost = Math.max(boost, (1 - ds / 3) * 0.6);
					for (const r of ripples) {
						const rdx = (x - r.x / cw) * 0.55, rdy = y - r.y / ch;
						const rd = Math.sqrt(rdx * rdx + rdy * rdy);
						const age = t - r.t0;
						const ring = Math.abs(rd - age * 16);
						if (ring < 1.6) boost = Math.max(boost, (1 - ring / 1.6) * (1 - age / 2.4));
					}

					const seed = seeds[y * cols + x];
					if (boost > 0.15) {
						const a = Math.min(0.85, 0.2 + boost * 0.7) * Math.max(mask, 0.35 * boost);
						if (a < 0.04) continue;
						const gi = Math.floor((seed * 7 + t * (0.6 + seed)) % CODE.length);
						hot.push(CODE[gi]);
						hotXY.push(x * cw, y * ch);
						hotA.push(a);
						continue;
					}

					const lvl = v * mask;
					const ri = Math.floor(lvl * (RAMP.length - 1) + seed * 0.35);
					if (ri <= 0) continue;
					const l = Math.min(LEVELS - 1, Math.floor(lvl * LEVELS));
					ambient[l].push(RAMP[Math.min(ri, RAMP.length - 1)]);
					ambientXY[l].push(x * cw, y * ch);
				}
			}

			ctx!.fillStyle = ink;
			for (let l = 0; l < LEVELS; l++) {
				const chars = ambient[l];
				if (!chars.length) continue;
				ctx!.globalAlpha = 0.07 + (l / (LEVELS - 1)) * 0.23;
				const xy = ambientXY[l];
				for (let i = 0; i < chars.length; i++) ctx!.fillText(chars[i], xy[i * 2], xy[i * 2 + 1]);
			}
			for (let i = 0; i < hot.length; i++) {
				ctx!.globalAlpha = hotA[i];
				ctx!.fillStyle = hotA[i] > 0.55 ? warm : accent;
				ctx!.fillText(hot[i], hotXY[i * 2], hotXY[i * 2 + 1]);
			}
			ctx!.globalAlpha = 1;
		}

		function loop(now: number) {
			raf = 0;
			if (!visible || document.hidden) return;
			if (now - last >= FRAME_MS) { last = now; draw(now); }
			raf = requestAnimationFrame(loop);
		}
		function start() {
			if (reduced.matches) { draw(performance.now()); return; }
			if (!raf) raf = requestAnimationFrame(loop);
		}
		function stop() { if (raf) cancelAnimationFrame(raf); raf = 0; }

		function local(e: PointerEvent) {
			const r = host!.getBoundingClientRect();
			return { x: e.clientX - r.left, y: e.clientY - r.top };
		}
		const onMove = (e: PointerEvent) => { pointer = local(e); if (reduced.matches) draw(performance.now()); };
		const onLeave = () => { pointer = null; if (reduced.matches) draw(performance.now()); };
		const onDown = (e: PointerEvent) => {
			if (reduced.matches) return;
			ripples.push({ ...local(e), t0: (performance.now() - t0) / 1000 });
			if (ripples.length > 4) ripples.shift();
		};
		const onVis = () => (document.hidden ? stop() : start());
		const onMotion = () => { stop(); start(); };

		readColors();
		resize();
		start();

		const ro = new ResizeObserver(() => { resize(); if (reduced.matches) draw(performance.now()); });
		ro.observe(host);
		const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; visible ? start() : stop(); });
		io.observe(host);
		// Theme switch (mode-watcher toggles the class on <html>).
		const mo = new MutationObserver(() => { readColors(); if (reduced.matches) draw(performance.now()); });
		mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });

		host.addEventListener('pointermove', onMove);
		host.addEventListener('pointerleave', onLeave);
		host.addEventListener('pointerdown', onDown);
		document.addEventListener('visibilitychange', onVis);
		reduced.addEventListener('change', onMotion);

		return () => {
			stop();
			ro.disconnect(); io.disconnect(); mo.disconnect();
			host.removeEventListener('pointermove', onMove);
			host.removeEventListener('pointerleave', onLeave);
			host.removeEventListener('pointerdown', onDown);
			document.removeEventListener('visibilitychange', onVis);
			reduced.removeEventListener('change', onMotion);
		};
	});
</script>

<canvas bind:this={canvas} class="ascii-field {className}" aria-hidden="true"></canvas>

<style>
	.ascii-field {
		position: absolute;
		inset: 0;
		pointer-events: none;
		user-select: none;
	}
</style>
