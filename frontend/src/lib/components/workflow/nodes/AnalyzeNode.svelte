<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import Search from '@lucide/svelte/icons/search';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	type ToolGroup = { label: string; tools: { slug: string; name: string }[] };
	const TOOL_GROUPS: ToolGroup[] = [
		{
			label: 'Static',
			tools: [
				{ slug: 'bandit', name: 'Bandit' },
				{ slug: 'eslint', name: 'ESLint' },
				{ slug: 'pylint', name: 'Pylint' }
			]
		},
		{
			label: 'Dynamic',
			tools: [
				{ slug: 'zap', name: 'ZAP' },
				{ slug: 'port_scanner', name: 'Ports' }
			]
		},
		{
			label: 'Perf',
			tools: [{ slug: 'lighthouse', name: 'Lighthouse' }]
		},
		{
			label: 'AI',
			tools: [{ slug: 'llm_review', name: 'LLM Review' }]
		}
	];

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const analyzers = $derived(((cfg.analyzers as string[]) ?? []) as string[]);
	const liveTarget = $derived((cfg.live_target as boolean) ?? false);
	const profileName = $derived((cfg.profile_name as string) ?? null);

	function isOn(slug: string): boolean {
		return analyzers.includes(slug);
	}
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="blue"
	icon={Search}
	kindLabel="Analyze"
	selected={selected ?? false}
>
	{#each TOOL_GROUPS as group (group.label)}
		<div class="flex items-start gap-2">
			<span class="text-muted-foreground w-12 shrink-0 pt-0.5 text-[10px] uppercase tracking-wider">{group.label}</span>
			<div class="flex flex-wrap gap-1">
				{#each group.tools as tool (tool.slug)}
					<span
						class="rounded px-1.5 py-0.5 text-[10px] border {isOn(tool.slug) ? 'bg-blue-500/20 border-blue-400/50 text-blue-200' : 'bg-background/40 border-border/60 text-muted-foreground/60'}"
					>{tool.name}</span>
				{/each}
			</div>
		</div>
	{/each}
	{#if profileName}
		<div class="mt-1 border-t border-current/10 pt-1">
			<span class="text-[10px] text-blue-200">⬡ {profileName}</span>
		</div>
	{/if}
	{#if liveTarget}
		<div class="mt-1 border-t border-current/10 pt-1">
			<span class="text-[10px] uppercase tracking-wider text-blue-300">● Live target</span>
		</div>
	{/if}
</NodeShell>
