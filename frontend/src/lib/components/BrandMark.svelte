<script lang="ts">
	/**
	 * BrandMark — the LLM Lab logo: a plotted-results glyph in a tile plus the
	 * serif wordmark. `compact` hides the wordmark (collapsed sidebar);
	 * `tagline` adds the mono subtitle under the name.
	 */
	import { cn } from '$lib/utils';

	interface Props {
		compact?: boolean;
		tagline?: boolean;
		size?: 'sm' | 'lg';
		class?: string;
	}

	let { compact = false, tagline = false, size = 'sm', class: className }: Props = $props();
</script>

<span class={cn('flex min-w-0 items-center gap-2.5', className)}>
	<span
		class={cn(
			'flex shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground',
			size === 'lg' ? 'size-11' : 'size-7'
		)}
		aria-hidden="true"
	>
		<svg viewBox="0 0 32 32" class={size === 'lg' ? 'size-8' : 'size-5'} fill="none">
			<path d="M8 8v16h16" stroke="currentColor" stroke-opacity=".45" stroke-width="1.6" stroke-linecap="round" />
			<path
				d="M11 20.5l4-5 3.5 2.5L24 10"
				stroke="currentColor"
				stroke-width="2.2"
				stroke-linecap="round"
				stroke-linejoin="round"
			/>
			<circle cx="11" cy="20.5" r="1.8" fill="currentColor" />
			<circle cx="15" cy="15.5" r="1.8" fill="currentColor" />
			<circle cx="18.5" cy="18" r="1.8" fill="currentColor" />
			<circle cx="24" cy="10" r="2.2" fill="#e0b252" />
		</svg>
	</span>
	{#if !compact}
		<span class="flex min-w-0 flex-col leading-none">
			<span
				class={cn(
					'truncate font-display font-semibold tracking-tight',
					size === 'lg' ? 'text-2xl' : 'text-[17px]'
				)}
			>
				LLM Lab
			</span>
			{#if tagline}
				<span class="mt-1 truncate font-mono text-[11px] uppercase tracking-[0.14em] opacity-60">
					Evaluation research
				</span>
			{/if}
		</span>
	{/if}
</span>
