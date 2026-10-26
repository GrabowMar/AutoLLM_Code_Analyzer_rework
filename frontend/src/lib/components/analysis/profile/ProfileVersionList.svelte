<script lang="ts">
	/**
	 * ProfileVersionList — every version of a profile slug, newest first, each
	 * linking to its (read-only unless latest) page.
	 *
	 * Props: `versions` (`AnalysisProfileDetail.versions`), `currentId` (the
	 * version being shown, highlighted), `latestVersion`, `label` (list name), `class`.
	 */
	import type { ProfileVersion } from '$lib/api/analysis';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { shortHash } from './profile-utils';

	interface Props {
		versions: ProfileVersion[];
		currentId?: string;
		latestVersion: number;
		label?: string;
		class?: string;
	}

	let {
		versions,
		currentId,
		latestVersion,
		label = 'Versions',
		class: className
	}: Props = $props();
</script>

<ol class={cn('space-y-1', className)} aria-label={label}>
	{#each versions as version (version.id)}
		{@const current = version.id === currentId}
		<li>
			<a
				href="/analysis/profiles/{version.id}"
				aria-current={current ? 'page' : undefined}
				class={cn(
					'flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded px-2 py-1.5 text-xs transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
					current
						? 'bg-primary/10 text-foreground'
						: 'text-muted-foreground hover:bg-muted hover:text-foreground'
				)}
			>
				<span class={cn('font-mono', current && 'font-semibold')}>v{version.version}</span>
				{#if version.version === latestVersion}<span class="text-[11px] text-primary">latest</span
					>{/if}
				{#if version.is_archived}<span class="text-[11px]">archived</span>{/if}
				<span class="ml-auto font-mono text-[11px]" title="Content hash {version.content_hash}"
					>#{shortHash(version.content_hash, 8)}</span
				>
				<span class="w-full text-[11px]">{formatDate(version.created_at)}</span>
			</a>
		</li>
	{/each}
</ol>
