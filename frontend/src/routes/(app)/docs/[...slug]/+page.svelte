<script lang="ts">
	import { page } from '$app/stores';
	import { onMount } from 'svelte';
	import { getDoc, getDocsTree } from '$lib/api/system';
	import type { DocPage, DocNode } from '$lib/api/system';
	import { prevNext, readingTime, flattenLeaves } from '$lib/docs/utils';
	import { metaFor } from '$lib/docs/categories';
	import type { Heading } from '$lib/docs/markdown';
	import MarkdownArticle from '$lib/components/docs/MarkdownArticle.svelte';
	import TocRail from '$lib/components/docs/TocRail.svelte';
	import PrevNext from '$lib/components/docs/PrevNext.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import Clock from '@lucide/svelte/icons/clock';
	import Calendar from '@lucide/svelte/icons/calendar';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Link from '@lucide/svelte/icons/link';
	import { toast } from 'svelte-sonner';

	let doc = $state<DocPage | null>(null);
	let tree = $state<DocNode[]>([]);
	let loading = $state(true);
	let notFound = $state(false);
	let headings = $state<Heading[]>([]);

	const currentSlug = $derived($page.params.slug as string);
	const meta = $derived(metaFor(doc?.category));
	const [prev, next] = $derived(prevNext(tree, currentSlug));
	const minutes = $derived(doc ? readingTime(doc.raw) : 0);

	async function loadDoc(slug: string) {
		loading = true;
		notFound = false;
		doc = null;
		headings = [];
		const result = await getDoc(slug);
		if (!result) {
			notFound = true;
		} else {
			doc = result;
		}
		loading = false;
	}

	onMount(async () => {
		tree = await getDocsTree();
	});

	$effect(() => {
		loadDoc($page.params.slug ?? '');
	});

	function formatDate(ts: number) {
		return new Date(ts * 1000).toLocaleDateString('en-US', {
			year: 'numeric',
			month: 'short',
			day: 'numeric',
		});
	}

	async function copyPermalink() {
		try {
			await navigator.clipboard.writeText(window.location.href);
			toast.success('Link copied');
		} catch {
			toast.error('Could not copy link');
		}
	}
</script>

<svelte:head>
	<title>{doc?.title ?? 'Docs'} · LLM Lab</title>
</svelte:head>

<div class="mx-auto flex w-full max-w-6xl gap-8 px-4 py-6 sm:px-6 sm:py-8 lg:gap-12 lg:px-10">
	<!-- Article -->
	<article class="min-w-0 flex-1">
		{#if loading}
			<LoadingState label="loading page" />
		{:else if notFound}
			<EmptyState icon={AlertCircle} title="Page not found" description="The document “{currentSlug}” does not exist.">
				{#snippet action()}
					<Button href="/docs" variant="outline" size="sm">Back to docs</Button>
				{/snippet}
			</EmptyState>
		{:else if doc}
			{@const Icon = meta.icon}
			<!-- Breadcrumb + meta row -->
			<nav aria-label="Breadcrumb" class="mb-4 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
				<a href="/docs" class="hover:text-foreground">Docs</a>
				<ChevronRight class="h-3 w-3" />
				<span class="inline-flex items-center gap-1 {meta.accent}">
					<Icon class="h-3 w-3" />
					{meta.label}
				</span>
				<ChevronRight class="h-3 w-3" />
				<span class="font-medium text-foreground truncate">{doc.title}</span>
			</nav>

			<div class="mb-6 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
				<Badge variant="outline" class="gap-1">
					<Calendar class="h-3 w-3" />
					Updated {formatDate(doc.last_modified)}
				</Badge>
				<Badge variant="outline" class="gap-1">
					<Clock class="h-3 w-3" />
					{minutes} min read
				</Badge>
				<button
					type="button"
					onclick={copyPermalink}
					class="inline-flex items-center gap-1 rounded-md border bg-background px-2 py-0.5 text-[11px] transition-colors hover:bg-muted"
					aria-label="Copy permalink"
				>
					<Link class="h-3 w-3" />
					Copy link
				</button>
			</div>

			<MarkdownArticle raw={doc.raw} onheadings={(h) => (headings = h)} />

			<PrevNext {prev} {next} />
		{/if}
	</article>

	<!-- TOC rail -->
	{#if doc && headings.length > 0}
		<aside class="hidden w-56 shrink-0 xl:block" aria-label="On this page">
			<div class="sticky top-[calc(3rem+1.5rem)] max-h-[calc(100dvh-3rem-3rem)] overflow-y-auto pr-2">
				<TocRail {headings} />
			</div>
		</aside>
	{/if}
</div>
