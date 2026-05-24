<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import Terminal from '@lucide/svelte/icons/terminal';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const keys = $derived(Object.keys(cfg).filter((k) => k !== '_position'));
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="gray"
	icon={Terminal}
	kindLabel="Script"
	selected={selected ?? false}
>
	{#if keys.length === 0}
		<p class="text-muted-foreground italic">Configure via JSON</p>
	{:else}
		<div class="space-y-0.5 font-mono text-[10px]">
			{#each keys.slice(0, 4) as key (key)}
				<div class="flex justify-between gap-2">
					<span class="text-muted-foreground truncate">{key}</span>
					<span class="truncate max-w-[120px]" title={String(cfg[key])}>{String(cfg[key]).slice(0, 20)}</span>
				</div>
			{/each}
			{#if keys.length > 4}<p class="text-muted-foreground/60">+{keys.length - 4} more…</p>{/if}
		</div>
	{/if}
</NodeShell>
