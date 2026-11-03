<script lang="ts" module>
	export interface RunFactor {
		/** Plural noun under the count ("app specs"); `singular` when the count is 1. */
		label: string;
		singular: string;
		/** `null` = not known yet (e.g. an empty required number field). */
		count: number | null;
		/** Anchor of the section that sets this factor; empty factors link there. */
		href?: string;
	}
</script>

<script lang="ts">
	/**
	 * RunFormula — the run matrix as arithmetic: `3 app specs × 2 models × 3
	 * trials = 18 runs`. Every factor is a tile; an empty one is dashed and
	 * links to the section that fills it, so the formula doubles as the
	 * "before you start" checklist (neutral — no error before the user acts).
	 *
	 * Props: `factors`, `total` (`null` while unknown), `unit` (plural, default
	 * "runs").
	 */
	import { cn } from '$lib/utils';

	let { factors, total, unit = 'runs' }: { factors: RunFactor[]; total: number | null; unit?: string } = $props();

	const missing = $derived(factors.filter((f) => f.count === 0));
	const totalUnit = $derived(total === 1 ? unit.replace(/s$/, '') : unit);
	const spoken = $derived(
		`${factors.map((f) => `${f.count ?? 'unknown'} ${f.count === 1 ? f.singular : f.label}`).join(' times ')} equals ${total ?? 'unknown'} ${totalUnit}`
	);
</script>

<div class="space-y-2">
	<p class="sr-only" aria-live="polite">{spoken}</p>
	<div class="flex flex-wrap items-stretch gap-x-1 gap-y-2" aria-hidden="true">
		{#each factors as f, i (f.label)}
			{#if i > 0}<span class="self-center px-0.5 font-mono text-sm text-muted-foreground">×</span>{/if}
			{@const empty = f.count === 0}
			<svelte:element
				this={empty && f.href ? 'a' : 'div'}
				href={empty ? f.href : undefined}
				tabindex={empty && f.href ? -1 : undefined}
				class={cn(
					'flex min-w-[3.25rem] flex-col items-center justify-center rounded-md border px-2 py-1.5 text-center',
					empty
						? 'cursor-pointer border-dashed border-border text-muted-foreground transition-colors duration-150 hover:border-primary/60 hover:text-foreground'
						: 'border-border bg-surface-2'
				)}
			>
				<span class="font-mono text-base leading-5 font-medium tabular-nums">{f.count ?? '—'}</span>
				<span class="text-[11px] leading-4 whitespace-nowrap text-muted-foreground">{f.count === 1 ? f.singular : f.label}</span>
			</svelte:element>
		{/each}
		<div class="flex items-stretch gap-x-1">
			<span class="self-center px-0.5 font-mono text-sm text-muted-foreground">=</span>
			<div
				class={cn(
					'flex min-w-[3.75rem] flex-col items-center justify-center rounded-md border px-2.5 py-1.5 text-center',
					total ? 'border-primary/40 bg-primary/10' : 'border-border'
				)}
			>
				<span class={cn('font-mono text-xl leading-6 font-medium tabular-nums', total ? 'text-primary' : 'text-muted-foreground')}
					>{total ?? '—'}</span
				>
				<span class="text-[11px] leading-4 text-muted-foreground">{totalUnit}</span>
			</div>
		</div>
	</div>
	{#if missing.length > 0}
		<p class="text-xs text-muted-foreground">
			Still to choose:
			{#each missing as f, i (f.label)}{#if i > 0}{i === missing.length - 1 ? ' and ' : ', '}{/if}{#if f.href}<a
						href={f.href}
						class="rounded font-medium text-foreground underline decoration-dotted underline-offset-2 hover:decoration-solid focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none"
						>{f.label}</a
					>{:else}{f.label}{/if}{/each}.
		</p>
	{/if}
</div>
