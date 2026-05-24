<script lang="ts">
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Search from '@lucide/svelte/icons/search';
	import FileText from '@lucide/svelte/icons/file-text';
	import Hourglass from '@lucide/svelte/icons/hourglass';
	import Bell from '@lucide/svelte/icons/bell';
	import Terminal from '@lucide/svelte/icons/terminal';
	import Plus from '@lucide/svelte/icons/plus';
	import type { Component } from 'svelte';
	import type { StepKind } from '$lib/utils/pipeline-flow';

	interface Props {
		onAdd?: (kind: StepKind) => void;
		compact?: boolean;
	}

	let { onAdd, compact = false }: Props = $props();

	interface PaletteItem {
		kind: StepKind;
		label: string;
		description: string;
		icon: Component;
		accent: string;
	}

	const ITEMS: PaletteItem[] = [
		{ kind: 'generate', label: 'Generate', description: 'LLM creates a sample app', icon: Sparkles, accent: 'text-violet-300 bg-violet-500/10 border-violet-500/40' },
		{ kind: 'analyze', label: 'Analyze', description: 'Run analyzer tools', icon: Search, accent: 'text-blue-300 bg-blue-500/10 border-blue-500/40' },
		{ kind: 'report', label: 'Report', description: 'Compile findings', icon: FileText, accent: 'text-amber-300 bg-amber-500/10 border-amber-500/40' },
		{ kind: 'wait', label: 'Wait', description: 'Pause execution', icon: Hourglass, accent: 'text-slate-300 bg-slate-500/10 border-slate-500/40' },
		{ kind: 'notify', label: 'Notify', description: 'Send a notification', icon: Bell, accent: 'text-emerald-300 bg-emerald-500/10 border-emerald-500/40' },
		{ kind: 'script', label: 'Script', description: 'Custom JSON step', icon: Terminal, accent: 'text-gray-300 bg-gray-500/10 border-gray-500/40' }
	];

	const ANALYZER_TOOLS = [
		{ slug: 'bandit', label: 'Bandit', group: 'Static' },
		{ slug: 'eslint', label: 'ESLint', group: 'Static' },
		{ slug: 'pylint', label: 'Pylint', group: 'Static' },
		{ slug: 'zap', label: 'OWASP ZAP', group: 'Dynamic' },
		{ slug: 'port_scanner', label: 'Port Scanner', group: 'Dynamic' },
		{ slug: 'lighthouse', label: 'Lighthouse', group: 'Performance' },
		{ slug: 'llm_review', label: 'LLM Review', group: 'AI' }
	];

	function onDragStart(event: DragEvent, kind: StepKind) {
		if (!event.dataTransfer) return;
		event.dataTransfer.setData('application/workflow-node', kind);
		event.dataTransfer.effectAllowed = 'move';
	}
</script>

<aside class="flex flex-col h-full {compact ? 'w-full' : 'w-64'} shrink-0 {compact ? '' : 'border-r'} bg-card/40 overflow-y-auto">
	{#if !compact}
		<div class="px-4 py-3 border-b">
			<h2 class="text-sm font-semibold">Node Palette</h2>
			<p class="text-xs text-muted-foreground mt-0.5">Drag onto canvas {onAdd ? '· or tap to add' : ''}</p>
		</div>
	{/if}

	<div class="p-3 {compact ? 'grid grid-cols-2 gap-2 space-y-0' : 'space-y-2'}">
		{#each ITEMS as item (item.kind)}
			<button
				type="button"
				draggable="true"
				ondragstart={(e) => onDragStart(e, item.kind)}
				onclick={() => onAdd?.(item.kind)}
				class="text-left w-full rounded-md border {item.accent} px-3 py-2.5 cursor-pointer active:scale-[0.98] hover:scale-[1.02] transition-transform select-none touch-manipulation focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary motion-reduce:transition-none motion-reduce:hover:scale-100 motion-reduce:active:scale-100 min-h-[44px]"
			>
				<div class="flex items-center gap-2">
					<item.icon class="h-4 w-4 shrink-0" />
					<span class="text-sm font-medium">{item.label}</span>
					{#if onAdd}
						<Plus class="h-3.5 w-3.5 ml-auto opacity-60" />
					{/if}
				</div>
				<p class="text-[11px] text-muted-foreground mt-0.5 leading-tight">{item.description}</p>
			</button>
		{/each}
	</div>

	{#if !compact}
		<div class="px-4 py-3 border-t border-b">
			<h3 class="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Analysis Tools</h3>
			<p class="text-[11px] text-muted-foreground mt-0.5">Toggle inside Analyze nodes</p>
		</div>

		<div class="p-3 space-y-3 text-xs">
			{#each ['Static', 'Dynamic', 'Performance', 'AI'] as group (group)}
				<div>
					<p class="text-[10px] uppercase tracking-wider text-muted-foreground mb-1.5">{group}</p>
					<div class="flex flex-wrap gap-1">
						{#each ANALYZER_TOOLS.filter((t) => t.group === group) as tool (tool.slug)}
							<span class="rounded border border-border bg-background/40 px-1.5 py-0.5 text-[10px] text-muted-foreground">{tool.label}</span>
						{/each}
					</div>
				</div>
			{/each}
		</div>

		<div class="mt-auto px-4 py-3 border-t text-[11px] text-muted-foreground">
			<p class="leading-snug">
				<strong class="text-foreground">Tip:</strong> Connect node outputs to inputs to set dependencies. Press <kbd class="rounded border px-1 bg-muted">Del</kbd> to remove selected items.
			</p>
		</div>
	{/if}
</aside>
