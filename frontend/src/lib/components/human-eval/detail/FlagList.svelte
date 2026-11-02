<script lang="ts">
	/**
	 * FlagList — data-quality flags (speeding, straight-lining, attention check,
	 * app not viewed, possible duplicate) as small warning chips with their
	 * definition as a tooltip; "—" when there are none.
	 *
	 * Props: `flags`.
	 */
	import { toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import { qualityFlagHelp, qualityFlagLabel } from '../format';

	let { flags }: { flags: string[] } = $props();
</script>

{#if flags.length === 0}
	<span class="text-xs text-muted-foreground">—</span>
{:else}
	<span class="flex flex-wrap gap-1">
		{#each flags as flag (flag)}
			<span
				class={cn('inline-flex items-center rounded border px-1.5 py-px text-[11px] leading-4', toneClasses.warning)}
				title={qualityFlagHelp(flag) || undefined}
			>
				{qualityFlagLabel(flag)}
			</span>
		{/each}
	</span>
{/if}
