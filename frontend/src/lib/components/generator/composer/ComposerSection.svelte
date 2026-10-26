<script lang="ts">
	/**
	 * ComposerSection — one numbered step of the composer form (a Card with a
	 * step badge, heading, one-line description and optional header actions).
	 *
	 * The badge carries the step's state: a check once a required step is
	 * `done`, an alert while it holds an `invalid` value. `meta` is a short
	 * right-aligned status ("3 selected").
	 *
	 * `collapsible` steps (optional settings) start folded when `open` is
	 * false and show `summary` — what applies right now — instead of their
	 * body. The body stays mounted (its fields keep validating) and an
	 * invalid step unfolds itself.
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import Check from '@lucide/svelte/icons/check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';

	interface Props {
		step: number;
		title: string;
		/** Section anchor; the heading id is `${id}-title`. */
		id: string;
		description?: string;
		/** Required steps: whether they are complete. */
		done?: boolean;
		invalid?: boolean;
		meta?: string;
		collapsible?: boolean;
		open?: boolean;
		/** One line shown while folded. */
		summary?: string;
		actions?: Snippet;
		children: Snippet;
	}

	let {
		step,
		title,
		id,
		description,
		done = false,
		invalid = false,
		meta,
		collapsible = false,
		open = $bindable(true),
		summary,
		actions,
		children,
	}: Props = $props();

	$effect(() => {
		if (invalid && collapsible) open = true;
	});

	const folded = $derived(collapsible && !open);
</script>

<section {id} aria-labelledby="{id}-title" class="scroll-mt-20">
	<Card.Root class={cn('gap-4', folded && 'gap-0', invalid && 'border-destructive/40')}>
		<Card.Header class={cn('flex-row items-start gap-3', folded && 'pb-0')}>
			<span
				class={cn(
					'mt-px flex size-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs font-semibold transition-colors duration-200',
					invalid
						? 'border-destructive/50 bg-destructive/10 text-danger-fg'
						: done
							? 'border-success/50 bg-success/10 text-success-fg'
							: 'border-primary/40 bg-primary/10 text-primary'
				)}
				aria-hidden="true"
			>
				{#if invalid}<CircleAlert class="size-3.5" />{:else if done}<Check class="size-3.5" />{:else}{step}{/if}
			</span>
			<div class="min-w-0 flex-1 space-y-1">
				<Card.Title>
					<h2 id="{id}-title" class="leading-6">
						{#if collapsible}
							<button
								type="button"
								class="group -mx-1 flex cursor-pointer items-center gap-1.5 rounded px-1 text-left focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
								aria-expanded={open}
								aria-controls="{id}-body"
								onclick={() => (open = !open)}
							>
								{title}
								<ChevronDown
									class={cn(
										'size-4 text-muted-foreground transition-transform duration-200 group-hover:text-foreground',
										open && 'rotate-180'
									)}
									aria-hidden="true"
								/>
							</button>
						{:else}
							{title}
						{/if}
						<span class="sr-only">{invalid ? '(has a field to fix)' : done ? '(done)' : ''}</span>
					</h2>
				</Card.Title>
				{#if folded && summary}
					<p class="text-xs text-muted-foreground sm:truncate" title={summary}>{summary}</p>
				{:else if description}
					<Card.Description class="text-xs">{description}</Card.Description>
				{/if}
			</div>
			{#if meta || actions}
				<div class="flex shrink-0 flex-wrap items-center justify-end gap-2">
					{#if meta}
						<span class={cn('font-mono text-xs tabular-nums', done ? 'text-foreground' : 'text-muted-foreground')}>{meta}</span>
					{/if}
					{@render actions?.()}
				</div>
			{/if}
		</Card.Header>
		<Card.Content id="{id}-body" class="@container space-y-4" hidden={folded}>
			{@render children()}
		</Card.Content>
	</Card.Root>
</section>
