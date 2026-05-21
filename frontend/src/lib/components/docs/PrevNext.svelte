<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';

	interface Page {
		slug: string;
		title: string;
	}

	interface Props {
		prev: Page | null;
		next: Page | null;
	}

	let { prev, next }: Props = $props();
</script>

{#if prev || next}
	<nav
		aria-label="Page navigation"
		class="mt-12 grid gap-3 border-t pt-6 sm:grid-cols-2"
	>
		{#if prev}
			<a
				href="/docs/{prev.slug}"
				class="group flex flex-col gap-1 rounded-lg border bg-card p-4 transition-colors hover:border-primary/50 hover:bg-muted/40"
			>
				<span class="flex items-center gap-1 text-xs text-muted-foreground">
					<ArrowLeft class="h-3 w-3" />
					Previous
				</span>
				<span class="text-sm font-medium group-hover:text-primary">{prev.title}</span>
			</a>
		{:else}
			<span></span>
		{/if}
		{#if next}
			<a
				href="/docs/{next.slug}"
				class="group flex flex-col items-end gap-1 rounded-lg border bg-card p-4 text-right transition-colors hover:border-primary/50 hover:bg-muted/40 sm:col-start-2"
			>
				<span class="flex items-center gap-1 text-xs text-muted-foreground">
					Next
					<ArrowRight class="h-3 w-3" />
				</span>
				<span class="text-sm font-medium group-hover:text-primary">{next.title}</span>
			</a>
		{/if}
	</nav>
{/if}
