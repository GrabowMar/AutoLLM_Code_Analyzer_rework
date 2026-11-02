<script lang="ts">
	/**
	 * SplitHandle — the vertical divider between the workbench panes: drag it,
	 * or focus it and use ←/→ (Home/End for the limits), to resize. `value` is
	 * the left pane's share in percent.
	 *
	 * Props: `value` (bindable), `min`, `max`, `container` (the element whose
	 * width the percentage refers to), bindable `dragging`, `onchange(value)`.
	 */
	import GripVertical from '@lucide/svelte/icons/grip-vertical';

	interface Props {
		value: number;
		min?: number;
		max?: number;
		container: HTMLElement | null;
		dragging?: boolean;
		onchange?: (value: number) => void;
	}

	let { value = $bindable(), min = 25, max = 75, container, dragging = $bindable(false), onchange }: Props = $props();

	const clamp = (v: number) => Math.min(max, Math.max(min, Math.round(v * 10) / 10));

	function set(v: number) {
		const next = clamp(v);
		if (next === value) return;
		value = next;
		onchange?.(next);
	}

	function onPointerDown(event: PointerEvent) {
		if (!container || event.button !== 0) return;
		event.preventDefault();
		(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
		dragging = true;
	}

	function onPointerMove(event: PointerEvent) {
		if (!dragging || !container) return;
		const rect = container.getBoundingClientRect();
		if (rect.width <= 0) return;
		set(((event.clientX - rect.left) / rect.width) * 100);
	}

	function onPointerUp(event: PointerEvent) {
		if (!dragging) return;
		dragging = false;
		(event.currentTarget as HTMLElement).releasePointerCapture?.(event.pointerId);
	}

	function onKeyDown(event: KeyboardEvent) {
		const step = event.shiftKey ? 10 : 2;
		if (event.key === 'ArrowLeft') set(value - step);
		else if (event.key === 'ArrowRight') set(value + step);
		else if (event.key === 'Home') set(min);
		else if (event.key === 'End') set(max);
		else return;
		event.preventDefault();
	}
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions, a11y_no_noninteractive_tabindex (a focusable separator is the WAI-ARIA window splitter) -->
<div
	role="separator"
	aria-orientation="vertical"
	aria-label="Resize the app and form panes"
	aria-valuenow={Math.round(value)}
	aria-valuemin={min}
	aria-valuemax={max}
	tabindex="0"
	class="group relative flex w-3 shrink-0 cursor-col-resize touch-none items-center justify-center focus-visible:outline-none"
	onpointerdown={onPointerDown}
	onpointermove={onPointerMove}
	onpointerup={onPointerUp}
	onpointercancel={onPointerUp}
	onkeydown={onKeyDown}
>
	<span
		class="h-full w-px bg-border transition-colors duration-150 group-hover:bg-primary/60 group-focus-visible:bg-primary {dragging ? 'bg-primary' : ''}"
		aria-hidden="true"
	></span>
	<span
		class="absolute flex h-8 w-3 items-center justify-center rounded border border-border bg-card text-muted-foreground group-hover:text-foreground group-focus-visible:ring-2 group-focus-visible:ring-ring/40"
		aria-hidden="true"
	>
		<GripVertical class="size-3" />
	</span>
</div>
