<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import Bell from '@lucide/svelte/icons/bell';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const message = $derived(((cfg.message as string) ?? '').trim());
	const preview = $derived(message.length > 80 ? message.slice(0, 77) + '...' : message);
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="emerald"
	icon={Bell}
	kindLabel="Notify"
	selected={selected ?? false}
>
	{#if preview}
		<p class="text-foreground/80 italic leading-snug">{preview}</p>
	{:else}
		<p class="text-muted-foreground italic">No message set</p>
	{/if}
</NodeShell>
