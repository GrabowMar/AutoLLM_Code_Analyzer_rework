<script lang="ts">
	/**
	 * BlockCard — one design block in the workbench stepper: number, title, the
	 * researcher's instructions (sanitized markdown), what is still missing and
	 * the server's errors from the last submit, then the block's form.
	 *
	 * Props: `number`, `title`, `instructions?`, `status` (BlockStatus),
	 * `serverErrors?` (messages for this block), `readOnly`, `children`.
	 */
	import type { Snippet } from 'svelte';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import * as Card from '$lib/components/ui/card';
	import Markdown from '$lib/components/eval-portal/Markdown.svelte';
	import type { BlockStatus } from './completeness';

	interface Props {
		number: number;
		title: string;
		instructions?: string;
		status: BlockStatus;
		serverErrors?: string[];
		readOnly?: boolean;
		children: Snippet;
	}

	let { number, title, instructions = '', status, serverErrors = [], readOnly = false, children }: Props = $props();
	const headingId = $props.id();
</script>

<Card.Root class="gap-0 py-0" aria-labelledby={headingId}>
	<div class="flex flex-wrap items-start justify-between gap-2 border-b border-border px-4 py-3">
		<h2 id={headingId} class="flex min-w-0 items-baseline gap-2 font-display text-lg font-semibold leading-snug">
			<span class="font-mono text-xs font-medium text-muted-foreground tabular-nums">{number}.</span>
			<span class="min-w-0 break-words">{title}</span>
		</h2>
		{#if status.complete}
			<span class="inline-flex items-center gap-1 text-xs font-medium text-success-fg">
				<CircleCheck class="size-3.5" aria-hidden="true" />
				Complete
			</span>
		{:else if status.total > 0}
			<span class="font-mono text-xs text-muted-foreground tabular-nums" title="Answered so far">
				{status.done}/{status.total}
			</span>
		{/if}
	</div>

	<div class="space-y-4 px-4 py-4">
		{#if instructions.trim()}
			<Markdown source={instructions} class="text-sm" />
		{/if}

		{#if serverErrors.length > 0}
			<div role="alert" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-danger-fg">
				<p class="flex items-center gap-1.5 font-medium">
					<CircleAlert class="size-4 shrink-0" aria-hidden="true" />
					Not accepted yet
				</p>
				<ul class="mt-1 list-disc space-y-0.5 pl-6 text-xs">
					{#each serverErrors as message, i (i)}
						<li>{message}</li>
					{/each}
				</ul>
			</div>
		{/if}

		{@render children()}

		{#if !readOnly && !status.complete && status.issues.length > 0}
			<div class="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
				<p class="font-medium text-foreground">Still to do</p>
				<ul class="mt-1 list-disc space-y-0.5 pl-5">
					{#each status.issues.slice(0, 6) as issue, i (i)}
						<li>{issue}</li>
					{/each}
					{#if status.issues.length > 6}
						<li>…and {status.issues.length - 6} more</li>
					{/if}
				</ul>
			</div>
		{/if}
	</div>
</Card.Root>
