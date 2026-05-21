<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { Heading } from '$lib/docs/markdown';

	interface Props {
		headings: Heading[];
		containerSelector?: string;
	}

	let { headings, containerSelector = '.docs-article' }: Props = $props();

	let activeId = $state<string>('');
	let observer: IntersectionObserver | null = null;

	function setup() {
		if (observer) observer.disconnect();
		if (typeof window === 'undefined' || headings.length === 0) return;
		const root = document.querySelector(containerSelector);
		if (!root) return;
		const elements = headings
			.map((h) => root.querySelector<HTMLElement>(`#${CSS.escape(h.id)}`))
			.filter((el): el is HTMLElement => !!el);
		if (elements.length === 0) return;
		observer = new IntersectionObserver(
			(entries) => {
				const visible = entries
					.filter((e) => e.isIntersecting)
					.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
				if (visible[0]) {
					activeId = visible[0].target.id;
				} else {
					// Fallback: pick the last heading above the viewport.
					const above = elements
						.filter((el) => el.getBoundingClientRect().top < 100)
						.pop();
					if (above) activeId = above.id;
				}
			},
			{
				rootMargin: '-80px 0px -70% 0px',
				threshold: [0, 1],
			},
		);
		for (const el of elements) observer.observe(el);
		// Seed with the first heading.
		if (!activeId && elements[0]) activeId = elements[0].id;
	}

	$effect(() => {
		// Re-run when headings change (new doc loaded).
		const _ = headings.map((h) => h.id).join('|');
		queueMicrotask(setup);
	});

	onDestroy(() => observer?.disconnect());

	function handleClick(e: MouseEvent, id: string) {
		const el = document.getElementById(id);
		if (!el) return;
		e.preventDefault();
		el.scrollIntoView({ behavior: 'smooth', block: 'start' });
		history.replaceState(null, '', `#${id}`);
		activeId = id;
	}

	function indent(level: number): string {
		const steps = Math.max(0, level - 2);
		return ['', 'pl-3', 'pl-6', 'pl-9'][steps] ?? 'pl-9';
	}
</script>

{#if headings.length > 0}
	<nav aria-label="On this page" class="toc-rail text-xs">
		<p class="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
			On this page
		</p>
		<ul class="space-y-1 border-l border-border/70">
			{#each headings as h (h.id)}
				<li>
					<a
						href={`#${h.id}`}
						onclick={(e) => handleClick(e, h.id)}
						class="-ml-px block border-l-2 py-0.5 pl-3 transition-colors {indent(h.level)} {activeId === h.id
							? 'border-primary text-foreground font-medium'
							: 'border-transparent text-muted-foreground hover:text-foreground hover:border-border'}"
					>
						{h.text}
					</a>
				</li>
			{/each}
		</ul>
	</nav>
{/if}
