<script lang="ts">
	/**
	 * SummaryChecklist — the neutral "before you start" list a Run summary shows
	 * until the form is complete (no errors are raised before the user acts).
	 */
	import type { ChecklistItem } from './issues';
	import Circle from '@lucide/svelte/icons/circle';
	import CircleCheck from '@lucide/svelte/icons/circle-check';

	let { items }: { items: ChecklistItem[] } = $props();
</script>

<ul class="space-y-2" aria-label="Before you start">
	{#each items as item (item.label)}
		<li class="flex items-start gap-2 text-sm">
			{#if item.done}
				<CircleCheck class="mt-0.5 size-4 shrink-0 text-success-fg" aria-hidden="true" />
			{:else}
				<Circle class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
			{/if}
			<span class={item.done ? 'text-foreground' : 'text-muted-foreground'}>
				{#if !item.done && item.href}
					<a href={item.href} class="rounded underline decoration-dotted underline-offset-2 hover:text-foreground hover:decoration-solid focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40">{item.label}</a>
				{:else}
					{item.label}
				{/if}
				{#if item.done && item.detail}<span class="text-muted-foreground"> · {item.detail}</span>{/if}
				<span class="sr-only">{item.done ? '(done)' : '(to do)'}</span>
			</span>
		</li>
	{/each}
</ul>
