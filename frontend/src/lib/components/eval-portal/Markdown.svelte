<script lang="ts">
	/**
	 * Markdown — researcher-authored markdown (task instructions, consent text,
	 * scenario descriptions) rendered through the sanitizing pipeline
	 * (`$lib/docs/markdown.ts` renderMarkdown: marked + DOMPurify). The raw text
	 * shows until the async render finishes (and stays if it fails). External
	 * http(s) links open in a new tab with rel="noopener noreferrer"; links with
	 * other schemes lose their href. Never use it for participant- or
	 * app-supplied strings (render those as text).
	 *
	 * Props: `source` (markdown), `class` (classes on the outer wrapper, e.g. a
	 * max width).
	 */
	import '$lib/styles/docs.css';
	import { mode } from 'mode-watcher';
	import { toast } from 'svelte-sonner';
	import { renderMarkdown } from '$lib/docs/markdown';
	import { cn } from '$lib/utils';

	interface Props {
		source: string;
		class?: string;
	}

	let { source, class: className }: Props = $props();

	let html = $state<string | null>(null);
	let htmlFor = $state<string | null>(null);
	let hasMermaid = $state(false);
	let article = $state<HTMLElement | null>(null);
	let renderSeq = 0;

	const text = $derived(source ?? '');
	const showHtml = $derived(html !== null && htmlFor === text);

	function hardenLinks(input: string): string {
		if (typeof document === 'undefined') return input;
		const tpl = document.createElement('template');
		tpl.innerHTML = input;
		for (const a of tpl.content.querySelectorAll('a[href]')) {
			const href = (a.getAttribute('href') ?? '').trim();
			if (/^https?:\/\//i.test(href) || href.startsWith('//')) {
				a.setAttribute('target', '_blank');
				a.setAttribute('rel', 'noopener noreferrer');
			} else if (/^[a-z][a-z0-9+.-]*:/i.test(href) && !/^mailto:/i.test(href)) {
				a.removeAttribute('href');
			}
		}
		return tpl.innerHTML;
	}

	$effect(() => {
		const src = text;
		const mine = ++renderSeq;
		if (!src.trim()) {
			html = '';
			htmlFor = src;
			hasMermaid = false;
			return;
		}
		renderMarkdown(src)
			.then((rendered) => {
				if (mine !== renderSeq) return;
				html = hardenLinks(rendered.html);
				hasMermaid = rendered.hasMermaid;
				htmlFor = src;
			})
			.catch(() => {
				// Keep showing the plain text.
				if (mine === renderSeq) html = null;
			});
	});

	// Mermaid diagrams render after the HTML is in the DOM (and again on theme change).
	$effect(() => {
		const theme = mode.current === 'dark' ? 'dark' : 'light';
		if (!showHtml || !hasMermaid || !article) return;
		const el = article;
		void import('$lib/docs/mermaid')
			.then(({ renderMermaidIn }) => renderMermaidIn(el, theme))
			.catch(() => {});
	});

	async function onClick(event: MouseEvent) {
		const target = event.target as HTMLElement | null;
		const btn = target?.closest<HTMLButtonElement>('.code-block__copy');
		if (!btn) return;
		const label = btn.querySelector<HTMLElement>('.code-block__copy-label');
		try {
			await navigator.clipboard.writeText(btn.dataset.copy ?? '');
			if (label) label.textContent = 'Copied!';
			btn.classList.add('is-copied');
			setTimeout(() => {
				if (label) label.textContent = 'Copy';
				btn.classList.remove('is-copied');
			}, 1400);
		} catch {
			toast.error('Could not copy to clipboard');
		}
	}
</script>

<div class={cn('eval-md min-w-0', className)}>
	{#if showHtml}
		<!-- The click handler only delegates to the code blocks' real <button>s.
		     html is sanitized by renderMarkdown (DOMPurify). -->
		<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
		<div bind:this={article} class="docs-article" onclick={onClick}>{@html html}</div>
	{:else}
		<p class="whitespace-pre-wrap break-words text-sm leading-relaxed">{text}</p>
	{/if}
</div>

<style>
	.eval-md :global(.docs-article) {
		max-width: none;
		font-size: 0.9rem;
		line-height: 1.65;
		overflow-wrap: anywhere;
	}
	.eval-md :global(.docs-article > :first-child) {
		margin-top: 0;
	}
	.eval-md :global(.docs-article h1) {
		font-size: 1.3rem;
		border-bottom: 0;
		padding-bottom: 0;
		margin-top: 1.4em;
	}
	.eval-md :global(.docs-article h2) {
		font-size: 1.12rem;
		margin-top: 1.4em;
		border-bottom: 0;
		padding-bottom: 0;
	}
	.eval-md :global(.docs-article h3),
	.eval-md :global(.docs-article h4) {
		font-size: 1rem;
		margin-top: 1.2em;
	}
	.eval-md :global(.docs-article img) {
		max-width: 100%;
		height: auto;
	}
</style>
