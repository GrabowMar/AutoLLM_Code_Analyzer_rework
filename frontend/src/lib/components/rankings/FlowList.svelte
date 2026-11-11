<script lang="ts">
	/**
	 * The PRISMA-style frame flow: K* steps collapse catalog rows into canonical
	 * models, E* steps apply the eligibility filters. Each step shows what remains,
	 * the exclusions by reason (counts), and the excluded ids on demand.
	 *
	 * Props: `flow` (FlowStep[] from /rankings/sources/ or a selection payload), `class`.
	 */
	import type { FlowStep } from '$lib/api/rankings';
	import { cn } from '$lib/utils';
	import { exclusionLabel } from './mss';

	interface Props {
		flow: FlowStep[];
		class?: string;
	}

	let { flow, class: className }: Props = $props();

	function byCode(step: FlowStep): { label: string; n: number }[] {
		const counts = new Map<string, number>();
		for (const e of step.excluded) {
			const label = exclusionLabel(e.code).label;
			counts.set(label, (counts.get(label) ?? 0) + 1);
		}
		return [...counts.entries()].map(([label, n]) => ({ label, n })).sort((a, b) => b.n - a.n);
	}

	const max = $derived(Math.max(1, ...flow.map((s) => s.remaining)));
</script>

{#if flow.length === 0}
	<p class="text-xs text-muted-foreground">No flow recorded.</p>
{:else}
	<ol class={cn('flow', className)} aria-label="Frame flow">
		{#each flow as step (step.step)}
			{@const reasons = byCode(step)}
			<li class="flow-step">
				<span class="font-mono text-[11px] text-muted-foreground">{step.step}</span>
				<div class="min-w-0">
					<div class="flex items-baseline justify-between gap-3">
						<span class="truncate text-xs" title={step.label}>{step.label}</span>
						<span class="shrink-0 font-mono text-xs font-medium tabular-nums">{step.remaining.toLocaleString()}</span>
					</div>
					<span class="mt-1 block h-[3px] overflow-hidden rounded-full bg-muted" aria-hidden="true">
						<span class="block h-full rounded-full bg-muted-foreground/45" style:width="{(step.remaining / max) * 100}%"></span>
					</span>
					{#if step.excluded.length}
						<details class="mt-1">
							<summary class="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground">
								−{step.excluded.length}: {reasons.map((r) => `${r.label} ${r.n}`).join(' · ')}
							</summary>
							<ul class="mt-1 max-h-48 overflow-y-auto font-mono text-[11px] text-muted-foreground">
								{#each step.excluded as e, i (`${e.id}:${i}`)}
									{@const ex = exclusionLabel(e.code)}
									<li class="truncate" title={e.detail ?? undefined}>{e.id} <span class="opacity-70">· {ex.label}{ex.target ? ` ${ex.target}` : ''}</span></li>
								{/each}
							</ul>
						</details>
					{/if}
				</div>
			</li>
		{/each}
	</ol>
{/if}

<style>
	.flow {
		display: grid;
		gap: 0.5rem;
	}
	.flow-step {
		display: grid;
		grid-template-columns: 3.25rem minmax(0, 1fr);
		align-items: start;
		gap: 0.5rem;
	}
</style>
