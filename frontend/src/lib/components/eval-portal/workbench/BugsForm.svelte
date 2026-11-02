<script lang="ts">
	/**
	 * BugsForm — the bug log: per app (A and B in pairwise mode) the bug reports
	 * and, when the block requires no minimum, "I found no bugs in this app".
	 * The minimum number of reports applies to each app separately.
	 *
	 * Props: `assignmentId`, `block`, `answers` (BugsAnswers), `apps`,
	 * `observations` (all of the assignment), `readOnly`, `onchange(next)`,
	 * `onobservationsaved`, `onobservationdeleted`.
	 */
	import type { BugsAnswers, BugsBlock, Observation, PortalApp } from '$lib/api/types/human-eval';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ObservationList from './ObservationList.svelte';
	import { slotTitle } from './workbench';

	interface Props {
		assignmentId: string;
		block: BugsBlock;
		answers: BugsAnswers;
		apps: PortalApp[];
		observations: Observation[];
		readOnly?: boolean;
		onchange: (next: BugsAnswers) => void;
		onobservationsaved: (observation: Observation) => void;
		onobservationdeleted: (id: string) => void;
	}

	let {
		assignmentId,
		block,
		answers,
		apps,
		observations,
		readOnly = false,
		onchange,
		onobservationsaved,
		onobservationdeleted,
	}: Props = $props();

	const uid = $props.id();
	const noneFound = $derived(answers.none_found ?? {});
	const min = $derived(Math.max(0, block.min_reports ?? 0));

	function bugsOf(slot: string): Observation[] {
		return observations.filter((o) => o.kind === 'bug' && o.app_slot === slot);
	}

	function setNoneFound(slot: string, value: boolean) {
		onchange({ ...answers, none_found: { ...noneFound, [slot]: value } });
	}
</script>

<div class="space-y-5">
	<p class="text-xs text-muted-foreground">
		{#if min > 0}
			Report at least <b class="font-medium text-foreground">{min}</b> bug{min === 1 ? '' : 's'}{apps.length > 1 ? ' for each app' : ''}.
		{:else}
			Report every bug you find{apps.length > 1 ? ' in each app' : ''}, or confirm that you found none.
		{/if}
		A bug is anything that does not work as a user would expect: errors, broken pages, wrong data, confusing behaviour.
	</p>
	{#each apps as app (app.slot)}
		{@const bugs = bugsOf(app.slot)}
		<section class="space-y-2">
			{#if apps.length > 1}
				<SectionHeading size="sm" title={slotTitle(app)} count={bugs.length} />
			{/if}
			<ObservationList
				{assignmentId}
				kind="bug"
				slot={app.slot}
				blockId={block.id}
				appLabel={app.label}
				observations={bugs}
				{readOnly}
				addLabel={apps.length > 1 ? `Report a bug in ${app.slot}` : 'Report a bug'}
				onsaved={onobservationsaved}
				ondeleted={onobservationdeleted}
			/>
			{#if min === 0}
				<label class="flex items-center gap-2 text-sm" for="{uid}-{app.slot}">
					<Checkbox
						id="{uid}-{app.slot}"
						checked={bugs.length === 0 && noneFound[app.slot] === true}
						disabled={readOnly || bugs.length > 0}
						onCheckedChange={(v) => setNoneFound(app.slot, v === true)}
					/>
					<span class={bugs.length > 0 ? 'text-muted-foreground' : ''}>
						I tested {apps.length > 1 ? app.label : 'this app'} and found no bugs
					</span>
				</label>
			{/if}
		</section>
	{/each}
</div>
