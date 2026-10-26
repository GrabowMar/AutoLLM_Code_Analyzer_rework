<script lang="ts">
	/**
	 * TargetReasons — eligibility badge of a target plus its reasons
	 * (exclude first, then warnings, then info), each with its detail in a
	 * tooltip, and sample-resolution warnings.
	 *
	 * Props: `eligible`, `reasons`, `warnings` (sample warnings).
	 */
	import type { TargetReason } from '$lib/api/analysis';
	import { toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import Hint from '../Hint.svelte';
	import { reasonTone } from './format';

	interface Props {
		eligible: boolean;
		reasons: TargetReason[];
		warnings?: string[];
	}

	let { eligible, reasons, warnings = [] }: Props = $props();

	const ORDER: Record<string, number> = { exclude: 0, warn: 1, info: 2 };
	const sorted = $derived(
		[...reasons].sort((a, b) => (ORDER[a.severity] ?? 3) - (ORDER[b.severity] ?? 3))
	);
</script>

<div class="flex flex-wrap items-center gap-1">
	<span
		class={cn(
			'inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium',
			toneClasses[eligible ? 'success' : 'danger']
		)}
	>
		{eligible ? 'Eligible' : 'Excluded'}
	</span>
	{#each sorted as r, i (`${r.code}-${i}`)}
		<Hint underline={false} text={r.detail || r.code}>
			<span
				class={cn(
					'inline-flex items-center rounded-full border px-1.5 py-0.5 text-[11px]',
					toneClasses[reasonTone(r.severity)]
				)}
			>
				{r.code.replace(/_/g, ' ')}
			</span>
		</Hint>
	{/each}
	{#each warnings as w, i (i)}
		<Hint underline={false} text={w}>
			<span
				class={cn('inline-flex rounded-full border px-1.5 py-0.5 text-[11px]', toneClasses.warning)}
				>sample warning</span
			>
		</Hint>
	{/each}
</div>
