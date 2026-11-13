<script lang="ts">
	/**
	 * SummaryIssues — plan issues grouped by severity (errors block Freeze / Start; warnings and notes
	 * inform), each with its quick fixes.
	 *
	 * Props:
	 * - `issues`: summary rows (`fromIssues(plan.issues)` plus client checks).
	 * - `actions`: code → one extra `{label, href?, onclick?}` (e.g. "Open Budget preset").
	 * - `onfix(issue, fix)`: apply a server quick fix (`IssueFix`: "raise cap to $X", "reduce repeats to R").
	 * - `oncell(cellKey)`: open a concerned cell (the first one is linked).
	 * - `summaries`: registry summaries by code (titles for codes without a built-in title).
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import type { IssueFix } from '$lib/api/v3/types';
	import { groupIssues, issueTitle, SEVERITY_LABELS, type IssueAction, type SummaryIssue } from './issues';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Info from '@lucide/svelte/icons/info';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		issues: SummaryIssue[];
		actions?: Record<string, IssueAction>;
		onfix?: (issue: SummaryIssue, fix: IssueFix) => void;
		oncell?: (cellKey: string) => void;
		summaries?: Record<string, string>;
	}

	let { issues, actions = {}, onfix, oncell, summaries }: Props = $props();

	const groups = $derived(groupIssues(issues));
	const TONES = { error: toneClasses.danger, warn: toneClasses.warning, info: toneClasses.neutral } as const;
	const ICONS = { error: CircleAlert, warn: TriangleAlert, info: Info } as const;
	const linkClass =
		'inline-flex min-h-6 cursor-pointer items-center rounded font-medium text-primary underline underline-offset-2 hover:decoration-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40';
</script>

{#if groups.length > 0}
	<div class="space-y-3">
		{#each groups as group (group.severity)}
			{@const Icon = ICONS[group.severity]}
			<div class="space-y-1.5">
				<p class="section-label">{SEVERITY_LABELS[group.severity]} · {group.items.length}</p>
				<ul class="space-y-1.5">
					{#each group.items as issue, i (`${issue.code}-${i}`)}
						{@const action = actions[issue.code]}
						{@const firstCell = issue.cells?.[0]}
						<li class={cn('flex items-start gap-2 rounded-md border px-2.5 py-2 text-xs', TONES[group.severity])}>
							<Icon class="mt-px size-3.5 shrink-0" aria-hidden="true" />
							<div class="min-w-0 flex-1 space-y-0.5">
								<p class="font-medium">{issueTitle(issue.code, summaries)}{issue.subject ? ` · ${issue.subject}` : ''}</p>
								<p class="break-words text-foreground/80">{issue.detail}</p>
								<div class="flex flex-wrap gap-x-3 gap-y-0.5">
									{#if onfix}
										{#each issue.fixes ?? [] as fix (fix.label)}
											<button type="button" class={linkClass} onclick={() => onfix(issue, fix)}>{fix.label}</button>
										{/each}
									{/if}
									{#if oncell && firstCell}
										<button type="button" class={linkClass} onclick={() => oncell(firstCell)}>
											Open cell <span class="font-mono">{firstCell}</span>
										</button>
									{/if}
									{#if action}
										{#if action.href}
											<a href={action.href} class={linkClass}>{action.label}</a>
										{:else if action.onclick}
											<button type="button" class={linkClass} onclick={action.onclick}>{action.label}</button>
										{/if}
									{/if}
								</div>
							</div>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
{/if}
