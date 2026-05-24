<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import FileText from '@lucide/svelte/icons/file-text';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const reportType = $derived((cfg.report_type as string) ?? 'comprehensive');
	const template = $derived((cfg.template_slug as string) ?? '');
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="amber"
	icon={FileText}
	kindLabel="Report"
	selected={selected ?? false}
>
	<div class="flex justify-between gap-2">
		<span class="text-muted-foreground">Type</span>
		<span class="rounded bg-amber-500/20 border border-amber-400/40 text-amber-200 px-1.5 py-0.5 text-[10px] font-mono">{reportType}</span>
	</div>
	{#if template}
		<div class="flex justify-between gap-2">
			<span class="text-muted-foreground">Template</span>
			<span class="font-mono truncate max-w-[140px]" title={template}>{template}</span>
		</div>
	{/if}
</NodeShell>
