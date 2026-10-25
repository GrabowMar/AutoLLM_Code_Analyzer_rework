<script lang="ts">
	/**
	 * SelectionSummary — the selected models per endpoint as removable chips
	 * (`gpu-1: 12 · gpu-2: 10 · OpenRouter: 8`), and "Balance across
	 * endpoints" when some weights are served by more than one endpoint.
	 *
	 * Props:
	 * - `selectedIds` (bindable `Set<number>`): replaced, never mutated.
	 * - `byId`: catalog lookup. `canBalance`, `balancing`, `onbalance()`.
	 */
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import type { LLMModelSummary } from '$lib/api/models';
	import Scale from '@lucide/svelte/icons/scale';
	import X from '@lucide/svelte/icons/x';
	import { selectionByEndpoint } from './composer';

	interface Props {
		selectedIds: Set<number>;
		byId: ReadonlyMap<number, LLMModelSummary>;
		canBalance?: boolean;
		onbalance?: () => void;
	}

	let { selectedIds = $bindable(), byId, canBalance = false, onbalance }: Props = $props();

	const groups = $derived(selectionByEndpoint([...selectedIds], byId));

	function remove(ids: number[]) {
		const drop = new Set(ids);
		selectedIds = new Set([...selectedIds].filter((id) => !drop.has(id)));
	}
</script>

{#if groups.length > 0}
	<div class="flex flex-wrap items-center gap-1.5" aria-label="Selected models by endpoint">
		<span class="section-label mr-1">{selectedIds.size} selected</span>
		{#each groups as group (group.key)}
			<span
				class={cn(
					'inline-flex items-center gap-1.5 rounded-full border py-0.5 pl-2 pr-1 text-[11px]',
					group.online === false ? 'border-warning/40 bg-warning/10 text-warning-fg' : 'border-border bg-muted/40'
				)}
				title={group.ids.map((id) => byId.get(id)?.model_name ?? `#${id}`).join('\n')}
			>
				{#if group.online != null}
					<span class={cn('status-dot h-1.5 w-1.5', group.online ? 'bg-success' : 'bg-destructive')} aria-hidden="true"></span>
				{/if}
				<span class="font-mono">{group.label}</span>
				<span class="font-semibold tabular-nums">{group.ids.length}</span>
				<button
					type="button"
					class="cursor-pointer rounded-full p-0.5 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
					aria-label="Remove the {group.ids.length} models on {group.label}"
					onclick={() => remove(group.ids)}
				>
					<X class="size-3" />
				</button>
			</span>
		{/each}
		{#if canBalance}
			<Button
				variant="outline"
				size="sm"
				class="ml-auto h-7"
				title="Where the same weights are served by several endpoints, pick the endpoint per model that finishes the experiment soonest"
				onclick={() => onbalance?.()}
			>
				<Scale />Balance across endpoints
			</Button>
		{/if}
	</div>
{/if}
