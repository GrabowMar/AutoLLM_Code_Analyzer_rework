<script lang="ts" module>
	export interface ComposerNavSection {
		/** Anchor of the `ComposerSection`. */
		id: string;
		step: number;
		label: string;
		/** Required sections: whether they are complete. `undefined` = optional. */
		done?: boolean;
		/** Holds a value that wasn't applied (or a server field error). */
		invalid?: boolean;
	}
</script>

<script lang="ts">
	/**
	 * ComposerSectionNav — the composer's section index in the sticky rail:
	 * every numbered step as a link, the one in view highlighted, required
	 * steps marked done / to do and steps with invalid fields flagged, so
	 * "Fix the highlighted fields" says where.
	 *
	 * Props: `sections`.
	 */
	import { onMount } from 'svelte';
	import { cn } from '$lib/utils';
	import Circle from '@lucide/svelte/icons/circle';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';

	let { sections }: { sections: ComposerNavSection[] } = $props();

	let current = $state('');
	// After a click the clicked section stays current until the user scrolls by hand
	// (near the bottom the scroll position alone would name a later section).
	let pinned = false;

	/** Below the sticky app bar: a section counts as current once its top passes this line. */
	const TRIGGER_PX = 120;

	function update() {
		if (pinned) return;
		const els = sections.map((s) => document.getElementById(s.id)).filter((el): el is HTMLElement => !!el);
		if (els.length === 0) return;
		// At the bottom the last sections can never reach the line: the last one wins.
		const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
		if (atBottom) {
			current = els[els.length - 1].id;
			return;
		}
		let next = els[0].id;
		for (const el of els) {
			if (el.getBoundingClientRect().top <= TRIGGER_PX) next = el.id;
			else break;
		}
		current = next;
	}

	onMount(() => {
		let frame = 0;
		const onScroll = () => {
			cancelAnimationFrame(frame);
			frame = requestAnimationFrame(update);
		};
		const unpin = () => (pinned = false);
		const unpinKeys = (e: KeyboardEvent) => {
			if (['ArrowUp', 'ArrowDown', 'PageUp', 'PageDown', 'Home', 'End', ' '].includes(e.key)) pinned = false;
		};
		update();
		window.addEventListener('scroll', onScroll, { passive: true });
		window.addEventListener('resize', onScroll, { passive: true });
		window.addEventListener('wheel', unpin, { passive: true });
		window.addEventListener('touchmove', unpin, { passive: true });
		window.addEventListener('keydown', unpinKeys);
		return () => {
			cancelAnimationFrame(frame);
			window.removeEventListener('scroll', onScroll);
			window.removeEventListener('resize', onScroll);
			window.removeEventListener('wheel', unpin);
			window.removeEventListener('touchmove', unpin);
			window.removeEventListener('keydown', unpinKeys);
		};
	});

	function jump(event: MouseEvent, id: string) {
		const el = document.getElementById(id);
		if (!el) return;
		event.preventDefault();
		el.scrollIntoView({ block: 'start' });
		// Move keyboard focus with the view (WCAG 2.4.3): the section heading.
		const heading = document.getElementById(`${id}-title`);
		if (heading) {
			heading.setAttribute('tabindex', '-1');
			heading.focus({ preventScroll: true });
		}
		history.replaceState(history.state, '', `#${id}`);
		current = id;
		pinned = true;
	}
</script>

<nav aria-label="Form sections" class="rounded-md border border-border bg-card p-2 shadow-[var(--shadow-card)]">
	<ol class="grid grid-cols-2 gap-x-1 gap-y-0.5">
		{#each sections as s (s.id)}
			{@const active = current === s.id}
			<li class="min-w-0">
				<a
					href="#{s.id}"
					onclick={(e) => jump(e, s.id)}
					aria-current={active ? 'location' : undefined}
					class={cn(
						'flex min-h-7 min-w-0 items-center gap-1.5 rounded px-1.5 py-1 text-xs transition-colors duration-150',
						'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
						active ? 'bg-primary/10 text-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'
					)}
				>
					<span class={cn('w-4 shrink-0 text-right font-mono text-[11px] tabular-nums', active && 'text-primary')}>{s.step}</span>
					<span class="min-w-0 flex-1 truncate">{s.label}</span>
					{#if s.invalid}
						<CircleAlert class="size-3.5 shrink-0 text-danger-fg" aria-hidden="true" />
						<span class="sr-only">(has a field to fix)</span>
					{:else if s.done === true}
						<CircleCheck class="size-3.5 shrink-0 text-success-fg" aria-hidden="true" />
						<span class="sr-only">(done)</span>
					{:else if s.done === false}
						<Circle class="size-3.5 shrink-0 opacity-60" aria-hidden="true" />
						<span class="sr-only">(required, to do)</span>
					{/if}
				</a>
			</li>
		{/each}
	</ol>
</nav>
