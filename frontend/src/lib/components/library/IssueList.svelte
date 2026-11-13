<script lang="ts">
	/**
	 * IssueList — lint / validation issues as compact rows: severity dot + text, the registered code and
	 * the content path. Errors first. Renders nothing for an empty list unless `emptyText` is given.
	 *
	 * Props: `issues`, `emptyText`, `class`.
	 */
	import type { Issue } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';

	interface Props {
		issues: Issue[];
		emptyText?: string;
		class?: string;
	}

	let { issues, emptyText, class: className }: Props = $props();

	const ORDER = { error: 0, warn: 1, info: 2 } as const;
	const sorted = $derived([...issues].sort((a, b) => ORDER[a.severity] - ORDER[b.severity]));
	const tone = { error: 'text-danger-fg', warn: 'text-warning-fg', info: 'text-muted-foreground' } as const;
	const dot = { error: 'bg-destructive', warn: 'bg-warning', info: 'bg-muted-foreground' } as const;
</script>

{#if sorted.length}
	<ul class={cn('space-y-1', className)} aria-label="Issues">
		{#each sorted as issue, i (i)}
			<li class="flex items-start gap-2 text-xs">
				<span class={cn('mt-1.5 size-1.5 shrink-0 rounded-full', dot[issue.severity])} aria-hidden="true"></span>
				<span class="min-w-0">
					<span class={cn('font-medium', tone[issue.severity])}>{issue.severity}</span>
					<span>{issue.message || issue.code}</span>
					<span class="font-mono text-muted-foreground">{issue.code}{issue.path ? ` · ${issue.path}` : ''}</span>
				</span>
			</li>
		{/each}
	</ul>
{:else if emptyText}
	<p class={cn('text-xs text-muted-foreground', className)}>{emptyText}</p>
{/if}
