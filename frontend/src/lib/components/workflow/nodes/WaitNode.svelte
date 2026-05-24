<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const seconds = $derived((cfg.seconds as number) ?? 5);

	function formatDuration(s: number): string {
		if (s < 60) return `${s}s`;
		if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
		return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
	}
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="slate"
	icon={Hourglass}
	kindLabel="Wait"
	selected={selected ?? false}
>
	<div class="flex items-center justify-center py-2">
		<span class="font-mono text-2xl font-bold text-slate-200">{formatDuration(seconds)}</span>
	</div>
</NodeShell>
