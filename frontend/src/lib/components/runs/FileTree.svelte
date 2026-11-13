<script lang="ts">
	/**
	 * FileTree — a directory tree of run files with origin badges (provenance, neutral outline, never a status
	 * colour). Directories fold; the selected file is highlighted.
	 *
	 * Props: `nodes` (from `buildFileTree`), `selected` path, `onselect(path)`, `depth`, `showOrigin`,
	 * `extra(file)` → a short trailing text (e.g. "3 symbols").
	 */
	import FileTree from './FileTree.svelte';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import FileCode from '@lucide/svelte/icons/file-code';
	import { cn } from '$lib/utils';
	import type { RunFileOut } from '$lib/api/v3/types';
	import { ORIGIN_HELP, type TreeNode } from './format';

	interface Props {
		nodes: TreeNode[];
		selected?: string | null;
		onselect: (path: string) => void;
		depth?: number;
		showOrigin?: boolean;
		extra?: (file: RunFileOut) => string;
	}

	let { nodes, selected = null, onselect, depth = 0, showOrigin = true, extra }: Props = $props();

	let folded = $state<Set<string>>(new Set());

	function toggle(path: string) {
		const next = new Set(folded);
		if (next.has(path)) next.delete(path);
		else next.add(path);
		folded = next;
	}
</script>

<ul class={cn('text-xs', depth > 0 && 'ml-3 border-l border-border pl-1.5')} role={depth === 0 ? 'tree' : 'group'}>
	{#each nodes as node (node.path + (node.file ? ':f' : ':d'))}
		<li role="treeitem" aria-selected={node.file ? selected === node.path : undefined} aria-expanded={node.file ? undefined : !folded.has(node.path)}>
			{#if node.file}
				{@const file = node.file}
				<button
					type="button"
					class={cn(
						'flex w-full cursor-pointer items-center gap-1.5 rounded px-1.5 py-1 text-left transition-colors hover:bg-accent',
						selected === node.path && 'bg-primary/10 text-primary',
						(file.excluded || file.origin === 'deleted') && 'text-muted-foreground',
					)}
					onclick={() => onselect(node.path)}
					title={file.path}
				>
					<FileCode class="h-3.5 w-3.5 shrink-0 opacity-70" />
					<span class={cn('min-w-0 flex-1 truncate font-mono', file.origin === 'deleted' && 'line-through')}>{node.name}</span>
					{#if extra}
						{@const text = extra(file)}
						{#if text}<span class="shrink-0 text-[11px] text-muted-foreground">{text}</span>{/if}
					{/if}
					{#if file.is_test}<span class="shrink-0 rounded border border-border px-1 text-[11px] text-muted-foreground">test</span>{/if}
					{#if showOrigin}
						<span
							class="shrink-0 rounded border border-border px-1 text-[11px] text-muted-foreground"
							title={ORIGIN_HELP[file.origin] ?? file.origin}
						>
							{file.origin}
						</span>
					{/if}
				</button>
			{:else}
				<button
					type="button"
					class="flex w-full cursor-pointer items-center gap-1 rounded px-1 py-1 text-left font-mono text-muted-foreground hover:text-foreground"
					onclick={() => toggle(node.path)}
				>
					{#if folded.has(node.path)}<ChevronRight class="h-3.5 w-3.5" />{:else}<ChevronDown class="h-3.5 w-3.5" />{/if}
					<span class="truncate">{node.name}/</span>
				</button>
				{#if !folded.has(node.path)}
					<FileTree nodes={node.children} {selected} {onselect} depth={depth + 1} {showOrigin} {extra} />
				{/if}
			{/if}
		</li>
	{/each}
</ul>
