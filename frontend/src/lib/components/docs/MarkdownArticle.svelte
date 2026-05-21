<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import type { Snippet } from 'svelte';
	import { renderMarkdown } from '$lib/docs/markdown';
	import { renderMermaidIn } from '$lib/docs/mermaid';
	import type { Heading } from '$lib/docs/markdown';
	import { mode } from 'mode-watcher';
	import { toast } from 'svelte-sonner';

	interface Props {
		raw: string;
		onheadings?: (h: Heading[]) => void;
	}

	let { raw, onheadings }: Props = $props();

	let containerEl: HTMLElement;
	let html = $state('');
	let hasMermaid = $state(false);
	let isRendering = $state(true);
	let cleanupCopy: (() => void) | null = null;

	async function render(source: string) {
		isRendering = true;
		const rendered = await renderMarkdown(source);
		html = rendered.html;
		hasMermaid = rendered.hasMermaid;
		onheadings?.(rendered.headings);
		isRendering = false;
		// Wait for DOM to update before wiring up mermaid + copy buttons.
		await Promise.resolve();
		queueMicrotask(async () => {
			if (!containerEl) return;
			wireCopyButtons();
			if (hasMermaid) {
				await renderMermaidIn(containerEl, mode.current === 'dark' ? 'dark' : 'light');
			}
		});
	}

	function wireCopyButtons() {
		if (!containerEl) return;
		if (cleanupCopy) cleanupCopy();
		const handler = async (e: Event) => {
			const target = e.target as HTMLElement;
			const btn = target.closest('.code-block__copy') as HTMLButtonElement | null;
			if (!btn) return;
			const code = btn.dataset.copy ?? '';
			const label = btn.querySelector<HTMLElement>('.code-block__copy-label');
			try {
				await navigator.clipboard.writeText(code);
				const original = label?.textContent ?? 'Copy';
				if (label) label.textContent = 'Copied!';
				btn.classList.add('is-copied');
				setTimeout(() => {
					if (label) label.textContent = original;
					btn.classList.remove('is-copied');
				}, 1400);
			} catch {
				toast.error('Could not copy to clipboard');
			}
		};
		containerEl.addEventListener('click', handler);
		cleanupCopy = () => containerEl.removeEventListener('click', handler);
	}

	$effect(() => {
		// Re-render when source changes.
		render(raw);
	});

	// Re-theme mermaid on theme switch (only re-render that pipeline).
	$effect(() => {
		const _theme = mode.current;
		if (!containerEl || !hasMermaid) return;
		// Re-render mermaid blocks from already-rendered SVGs is non-trivial;
		// the simplest correct path is to re-run the whole markdown pipeline.
		render(raw);
	});

	onDestroy(() => {
		cleanupCopy?.();
	});
</script>

<div bind:this={containerEl} class="docs-article">
	{#if isRendering}
		<div class="space-y-3 animate-pulse">
			<div class="h-8 w-2/3 rounded bg-muted"></div>
			<div class="h-4 w-full rounded bg-muted"></div>
			<div class="h-4 w-5/6 rounded bg-muted"></div>
			<div class="h-4 w-4/6 rounded bg-muted"></div>
			<div class="h-32 w-full rounded bg-muted"></div>
		</div>
	{:else}
		{@html html}
	{/if}
</div>
