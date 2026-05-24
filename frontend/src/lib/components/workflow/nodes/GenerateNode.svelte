<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const model = $derived((cfg.model_id as string) ?? '');
	const template = $derived((cfg.template_slug as string) ?? '');
	const appNum = $derived((cfg.app_num as number) ?? 1);
	const hasPrompt = $derived(typeof cfg.prompt === 'string' && (cfg.prompt as string).length > 0);
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="violet"
	icon={Sparkles}
	kindLabel="Generate"
	selected={selected ?? false}
>
	<div class="flex justify-between gap-2">
		<span class="text-muted-foreground">Model</span>
		<span class="font-mono truncate max-w-[140px]" title={model}>{model || '—'}</span>
	</div>
	<div class="flex justify-between gap-2">
		<span class="text-muted-foreground">Template</span>
		<span class="font-mono truncate max-w-[140px]" title={template}>{template || '—'}</span>
	</div>
	<div class="flex justify-between gap-2">
		<span class="text-muted-foreground">App #</span>
		<span class="font-mono">{appNum}</span>
	</div>
	{#if hasPrompt}
		<div class="pt-1 mt-1 border-t border-current/10">
			<span class="text-violet-300 text-[10px] uppercase tracking-wider">Custom prompt</span>
		</div>
	{/if}
</NodeShell>
