<script lang="ts">
	/**
	 * Hint — inline content with an accessible tooltip (bits-ui Tooltip).
	 *
	 * The trigger is keyboard-focusable and the text is also in the DOM for
	 * screen readers (`sr-only`), so the explanation never depends on hover.
	 *
	 * Props:
	 * - `text`: the explanation (empty renders the children without a tooltip).
	 * - `children`: the inline content (a value, an icon, a badge).
	 * - `side`: tooltip side (default "top").
	 * - `underline`: dotted underline cue (default true; off for icons/badges).
	 * - `class`: classes on the trigger span.
	 */
	import type { Snippet } from 'svelte';
	import { Tooltip } from 'bits-ui';
	import { cn } from '$lib/utils';

	interface Props {
		text: string;
		children: Snippet;
		side?: 'top' | 'bottom' | 'left' | 'right';
		underline?: boolean;
		class?: string;
	}

	let { text, children, side = 'top', underline = true, class: className }: Props = $props();
</script>

{#if text}
	<Tooltip.Provider delayDuration={150} skipDelayDuration={200}>
		<Tooltip.Root>
			<Tooltip.Trigger>
				{#snippet child({ props })}
					<span
						{...props}
						role="button"
						tabindex="0"
						class={cn(
							'inline-flex cursor-help items-center gap-1 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
							underline &&
								'underline decoration-muted-foreground/50 decoration-dotted underline-offset-[3px]',
							className
						)}
					>
						{@render children()}
						<span class="sr-only">({text})</span>
					</span>
				{/snippet}
			</Tooltip.Trigger>
			<Tooltip.Portal>
				<Tooltip.Content
					{side}
					sideOffset={6}
					collisionPadding={8}
					class="z-50 max-w-72 rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs leading-snug text-popover-foreground shadow-md"
				>
					{text}
				</Tooltip.Content>
			</Tooltip.Portal>
		</Tooltip.Root>
	</Tooltip.Provider>
{:else}
	<span class={cn('inline-flex items-center gap-1', className)}>
		{@render children()}
	</span>
{/if}
