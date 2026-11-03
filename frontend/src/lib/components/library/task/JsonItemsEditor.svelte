<script lang="ts">
	/**
	 * A list of structured items edited as one JSON document each (hand-written acceptance checks, CWE
	 * probes): a summary line per item (`summarize`), expandable to its JSON with a parse error, plus
	 * "Add" with a template (`newItem`). Edits the bound `items` (texts) in place.
	 */
	import { Button } from '$lib/components/ui/button';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import type { TaskErrors } from '../task';

	interface Props {
		items: string[];
		/** Error-map prefix (`acceptance`, `probes`). */
		path: string;
		noun: string;
		newItem: () => string;
		summarize: (item: Record<string, unknown>) => string;
		errors?: TaskErrors;
		readonly?: boolean;
		empty?: string;
	}

	let { items = $bindable(), path, noun, newItem, summarize, errors = {}, readonly = false, empty }: Props = $props();

	let open = $state<Record<number, boolean>>({});

	function summary(text: string): string {
		try {
			return summarize(JSON.parse(text) as Record<string, unknown>);
		} catch {
			return 'invalid JSON';
		}
	}

	function add() {
		items = [...items, newItem()];
		open = { ...open, [items.length - 1]: true };
	}
</script>

<div class="space-y-2">
	{#if items.length === 0}
		<p class="text-sm text-muted-foreground">{empty ?? `No ${noun}s.`}</p>
	{:else}
		<ul class="divide-y divide-border rounded-md border border-border">
			{#each items as _item, i (i)}
				{@const isOpen = open[i] ?? false}
				{@const error = errors[`${path}.${i}`]}
				<li>
					<button type="button" class="flex w-full cursor-pointer items-center gap-2 px-3 py-2 text-left hover:bg-muted/50" aria-expanded={isOpen} onclick={() => (open = { ...open, [i]: !isOpen })}>
						<ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform duration-150 {isOpen ? 'rotate-90' : ''}" aria-hidden="true" />
						<span class="min-w-0 flex-1 truncate font-mono text-xs">{summary(items[i])}</span>
						{#if error}<span class="text-[11px] text-danger-fg">fix</span>{/if}
					</button>
					{#if isOpen}
						<div class="space-y-1.5 border-t border-border/70 px-3 py-2">
							<textarea
								bind:value={items[i]}
								readonly={readonly}
								rows={14}
								spellcheck="false"
								aria-label="{noun} {i + 1} (JSON)"
								class="block w-full rounded-md border border-input bg-surface-1 px-2 py-1.5 font-mono text-xs read-only:bg-muted/30"
							></textarea>
							{#if error}<p class="text-xs text-danger-fg">{error}</p>{/if}
							{#if !readonly}
								<div class="flex justify-end">
									<Button variant="ghost" size="sm" onclick={() => (items = items.filter((_, j) => j !== i))}><Trash2 />Remove {noun}</Button>
								</div>
							{/if}
						</div>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
	{#if !readonly}
		<Button variant="outline" size="sm" onclick={add}><Plus />Add {noun}</Button>
	{/if}
</div>
