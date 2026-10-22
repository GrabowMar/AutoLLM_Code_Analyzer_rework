<script lang="ts">
	/**
	 * SummaryIssues — run-summary issues grouped by severity (errors block the
	 * start; warnings and notes inform). Optional per-code quick actions.
	 *
	 * Props: `issues`, `actions` (code → `{ label, href? , onclick? }`).
	 */
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import { groupIssues, issueTitle, SEVERITY_LABELS, type IssueAction, type SummaryIssue } from './issues';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Info from '@lucide/svelte/icons/info';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		issues: SummaryIssue[];
		actions?: Record<string, IssueAction>;
	}

	let { issues, actions = {} }: Props = $props();

	const groups = $derived(groupIssues(issues));
	const TONES = { error: toneClasses.danger, warn: toneClasses.warning, info: toneClasses.neutral } as const;
	const ICONS = { error: CircleAlert, warn: TriangleAlert, info: Info } as const;
	const linkClass =
		'cursor-pointer rounded font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40';
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
						<li class={cn('flex items-start gap-2 rounded-md border px-2.5 py-2 text-xs', TONES[group.severity])}>
							<Icon class="mt-px size-3.5 shrink-0" aria-hidden="true" />
							<div class="min-w-0 flex-1 space-y-0.5">
								<p class="font-medium">{issueTitle(issue.code)}{issue.subject ? ` · ${issue.subject}` : ''}</p>
								<p class="break-words text-foreground/80">{issue.detail}</p>
								{#if action}
									{#if action.href}
										<a href={action.href} class={linkClass}>{action.label}</a>
									{:else if action.onclick}
										<button type="button" class={linkClass} onclick={action.onclick}>{action.label}</button>
									{/if}
								{/if}
							</div>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	</div>
{/if}
