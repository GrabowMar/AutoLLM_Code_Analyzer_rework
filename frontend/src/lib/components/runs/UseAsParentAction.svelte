<script lang="ts">
	/**
	 * UseAsParentAction — the per-run "Use as parent" action (spec 10.4) for study Results/Data tables and the
	 * sample page: opens the slot picker on this run (`/slots/new?parent=<id>`), where it is frozen as a
	 * SlotSet. Only a completed, usable `app_agentic` run with outcome `tested` qualifies; other rows render a
	 * disabled button saying why (or nothing with `hideWhenIneligible`).
	 *
	 * Props: `runId`; `run` (`{kind, status, usable, outcome}`, optional: without it the action is shown and the
	 * picker validates); `label` ("Use as parent"); `variant` (`ghost` for table rows); `size`;
	 * `hideWhenIneligible`.
	 */
	import { Button } from '$lib/components/ui/button';
	import GitBranch from '@lucide/svelte/icons/git-branch';
	import { canBeParent } from './format';

	interface Props {
		runId: string;
		run?: { kind: string; status: string; usable: boolean | null; outcome: string } | null;
		label?: string;
		variant?: 'ghost' | 'outline' | 'default';
		size?: 'sm' | 'default';
		hideWhenIneligible?: boolean;
		class?: string;
	}

	let {
		runId,
		run = null,
		label = 'Use as parent',
		variant = 'ghost',
		size = 'sm',
		hideWhenIneligible = false,
		class: className,
	}: Props = $props();

	const eligible = $derived(run === null || canBeParent(run));
	const reason = $derived(
		!run || eligible
			? 'Freeze this app and regenerate a part of it N times'
			: run.kind !== 'app_agentic'
				? 'Only agentic app runs can be parents'
				: run.status !== 'completed'
					? 'The run has not finished'
					: run.outcome !== 'tested'
						? `Parents must be tested (this run is ${run.outcome || 'without an outcome'})`
						: 'The run is not usable',
	);
	const href = $derived(`/slots/new?parent=${encodeURIComponent(runId)}`);
</script>

{#if eligible}
	<Button {href} {variant} {size} class={className} title={reason}>
		<GitBranch class="mr-1.5 h-3.5 w-3.5" />{label}
	</Button>
{:else if !hideWhenIneligible}
	<Button {variant} {size} class={className} disabled title={reason} aria-label={`${label}: ${reason}`}>
		<GitBranch class="mr-1.5 h-3.5 w-3.5" />{label}
	</Button>
{/if}
