<script lang="ts">
	/**
	 * IssueList — preflight problems of one severity (blocking / warnings /
	 * info) with their codes, details and the extra context some codes carry
	 * (tools, exclusion reason counts, affected jobs). `action` renders a
	 * remedy next to an issue (e.g. "Build image" for `image_missing`).
	 *
	 * Props: `issues`, `tone` ('danger' | 'warning' | 'info' | 'neutral'),
	 * `title`, `action` snippet `(issue) => …`, `empty` (text when none).
	 */
	import type { Snippet } from 'svelte';
	import type { PreflightIssue } from '$lib/api/analysis';
	import { toneClasses, type Tone } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import OctagonX from '@lucide/svelte/icons/octagon-x';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Info from '@lucide/svelte/icons/info';
	import { humanize, shortHash } from './format';

	interface Props {
		issues: PreflightIssue[];
		tone: Tone;
		title: string;
		action?: Snippet<[PreflightIssue]>;
		empty?: string;
	}

	let { issues, tone, title, action, empty = '' }: Props = $props();

	const Icon = $derived(tone === 'danger' ? OctagonX : tone === 'warning' ? TriangleAlert : Info);
</script>

{#if issues.length > 0 || empty}
	<section class="space-y-2" aria-label={title}>
		<h4 class="flex items-center gap-1.5 text-sm font-semibold">
			<Icon
				class={cn(
					'h-4 w-4',
					tone === 'danger'
						? 'text-danger-fg'
						: tone === 'warning'
							? 'text-warning-fg'
							: 'text-info-fg'
				)}
				aria-hidden="true"
			/>
			{title}
			<span class="text-xs font-normal tabular-nums text-muted-foreground">{issues.length}</span>
		</h4>
		{#if issues.length === 0}
			<p class="text-xs text-muted-foreground">{empty}</p>
		{:else}
			<ul class="space-y-2">
				{#each issues as issue, i (`${issue.code}-${i}`)}
					<li class={cn('rounded-md border px-3 py-2 text-sm', toneClasses[tone])}>
						<div class="flex flex-wrap items-start justify-between gap-2">
							<div class="min-w-0 space-y-1">
								<p class="font-medium text-foreground">
									{humanize(issue.code)}
								</p>
								<p class="break-words text-xs text-foreground/80">
									{issue.detail}
								</p>
								{#if issue.tools?.length}
									<ul class="flex flex-wrap gap-1">
										{#each issue.tools as tool (tool)}<li class="mono-tag">
												{tool}
											</li>{/each}
									</ul>
								{/if}
								{#if issue.reasons && Object.keys(issue.reasons).length > 0}
									<ul class="flex flex-wrap gap-1 text-[11px]">
										{#each Object.entries(issue.reasons) as [reason, n] (reason)}
											<li class="mono-tag">
												{reason.replace(/_/g, ' ')} · {n}
											</li>
										{/each}
									</ul>
								{/if}
								{#if issue.field_errors && Object.keys(issue.field_errors).length > 0}
									<ul class="space-y-0.5 text-[11px] text-foreground/80">
										{#each Object.entries(issue.field_errors) as [field, msg] (field)}
											<li><span class="font-mono">{field}</span>: {msg}</li>
										{/each}
									</ul>
								{/if}
								{#if issue.job_ids?.length}
									<p class="text-[11px] text-foreground/70">
										Jobs:
										{#each issue.job_ids.slice(0, 12) as job, k (job)}
											{#if k > 0},{/if}
											<a class="font-mono hover:underline" href="/applications/{job}"
												>{shortHash(job, 8)}</a
											>
										{/each}
										{#if issue.job_ids.length > 12}and {issue.job_ids.length - 12} more{/if}
									</p>
								{/if}
							</div>
							{#if action}
								<div class="shrink-0">{@render action(issue)}</div>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}
