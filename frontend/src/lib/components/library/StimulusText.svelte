<script lang="ts">
	/**
	 * StimulusText — one rendered stimulus with its source spans highlighted by owner (D10: every span of
	 * rendered text is traceable to the library entity that owns it). A legend lists the owners; hovering
	 * or focusing a legend entry emphasises its spans; clicking a span calls `onspan` (WP-15's cell drawer
	 * opens the override dialog for that owner), else it links to the owner's Library page.
	 *
	 * Props: `stimulus` (`StimulusOut`), `onspan?`, `activeOwner?` (`"<owner_kind>:<owner_ref>"`),
	 * `maxHeight` (CSS length of the text box, default 32rem).
	 */
	import type { SourceSpanOut, StimulusOut } from '$lib/api/v3/types';
	import { shortRef } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import { OWNER_LABEL, ownerHref, ownerSlot, segmentStimulus, stimulusOwners } from './spans';

	interface Props {
		stimulus: StimulusOut;
		onspan?: (source: SourceSpanOut) => void;
		activeOwner?: string | null;
		maxHeight?: string;
	}

	let { stimulus, onspan, activeOwner = null, maxHeight = '32rem' }: Props = $props();

	const segments = $derived(segmentStimulus(stimulus));
	const owners = $derived(stimulusOwners(stimulus));
	let hovered = $state<string | null>(null);
	const focus = $derived(hovered ?? activeOwner);

	const keyOf = (s: Pick<SourceSpanOut, 'owner_kind' | 'owner_ref'>) => `${s.owner_kind}:${s.owner_ref}`;
	const spanTitle = (s: SourceSpanOut) =>
		`${OWNER_LABEL[s.owner_kind]} ${s.owner_ref}${s.template_ref !== s.owner_ref ? ` · template ${s.template_ref}` : ''}`;
	const tint = (slot: number, strong: boolean) =>
		`background: color-mix(in oklab, var(--chart-${slot + 1}) ${strong ? 30 : 13}%, transparent);`;
</script>

<div class="space-y-2">
	<ul class="flex flex-wrap gap-1.5" aria-label="Sources">
		{#each owners as o (keyOf(o))}
			{@const href = ownerHref({ ...o, template_ref: o.owner_ref })}
			<li>
				<a
					href={href ?? undefined}
					class="inline-flex items-center gap-1.5 rounded-md border border-border px-1.5 py-0.5 text-[11px] transition-colors duration-150 hover:border-primary/50"
					onmouseenter={() => (hovered = keyOf(o))}
					onmouseleave={() => (hovered = null)}
					onfocus={() => (hovered = keyOf(o))}
					onblur={() => (hovered = null)}
				>
					<span class="size-2.5 rounded-sm" style={tint(ownerSlot(o.owner_kind), true)} aria-hidden="true"></span>
					<span>{OWNER_LABEL[o.owner_kind]}</span>
					<span class="font-mono text-muted-foreground">{shortRef(o.owner_ref)}</span>
					<span class="font-mono text-muted-foreground tabular-nums">{o.chars}</span>
				</a>
			</li>
		{/each}
	</ul>
	<pre
		class="overflow-auto whitespace-pre-wrap break-words rounded-md border border-border bg-surface-1 p-3 font-mono text-xs leading-relaxed"
		style="max-height: {maxHeight}">{#each segments as seg (seg.start)}{#if seg.source}{@const k = keyOf(seg.source)}{@const src = seg.source}{#if onspan}<span
					role="button"
					tabindex="0"
					class={cn('cursor-pointer rounded-[2px]', focus && focus !== k && 'opacity-60')}
					style={tint(ownerSlot(src.owner_kind), focus === k)}
					title={spanTitle(src)}
					onclick={() => onspan(src)}
					onkeydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') {
							e.preventDefault();
							onspan(src);
						}
					}}>{seg.text}</span
				>{:else}<span class={cn('rounded-[2px]', focus && focus !== k && 'opacity-60')} style={tint(ownerSlot(src.owner_kind), focus === k)} title={spanTitle(src)}
					>{seg.text}</span
				>{/if}{:else}<span class="text-muted-foreground">{seg.text}</span>{/if}{/each}</pre>
	<p class="font-mono text-[11px] text-muted-foreground">
		{stimulus.role} · {stimulus.chars.toLocaleString('en-US')} chars · ≈{stimulus.est_tokens.toLocaleString('en-US')} tokens · sha {stimulus.sha.slice(0, 12)}
	</p>
</div>
