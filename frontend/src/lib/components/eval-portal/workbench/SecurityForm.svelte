<script lang="ts">
	/**
	 * SecurityForm — a security block: the guided checklist (an outcome per item,
	 * "How to test" help, optional note), vulnerability reports (if the block
	 * allows them) and the TP/FP review of sampled automated findings.
	 *
	 * Props: `assignmentId`, `block`, `answers`, `context`, `apps`,
	 * `observations` (all of the assignment), `verdicts`, `verdictSaver` (the
	 * workbench's finding-verdict saver), `readOnly`, `onchange(next)`,
	 * `onobservationsaved`, `onobservationdeleted`, `onverdict`, `canOpenSource`,
	 * `onopensource`, `consoleAvailable`.
	 */
	import TerminalSquare from '@lucide/svelte/icons/square-terminal';
	import type {
		BlockContext,
		ChecklistOutcome,
		FindingVerdictValue,
		Observation,
		PortalApp,
		SecurityAnswers,
		SecurityBlock,
	} from '$lib/api/types/human-eval';
	import { Textarea } from '$lib/components/ui/textarea';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ChoiceGroup, { type ChoiceOption } from './ChoiceGroup.svelte';
	import FindingReview from './FindingReview.svelte';
	import ObservationList from './ObservationList.svelte';
	import type { BlockAutosave } from './autosave.svelte';
	import type { Slot } from './workbench';

	type Entry = { verdict: FindingVerdictValue; note: string };

	interface Props {
		assignmentId: string;
		block: SecurityBlock;
		answers: SecurityAnswers;
		context: BlockContext | undefined;
		apps: PortalApp[];
		observations: Observation[];
		verdicts: Record<string, Entry>;
		verdictSaver: BlockAutosave<Entry>;
		readOnly?: boolean;
		consoleAvailable?: boolean;
		onchange: (next: SecurityAnswers) => void;
		onobservationsaved: (observation: Observation) => void;
		onobservationdeleted: (id: string) => void;
		onverdict: (findingId: string, entry: Entry) => void;
		canOpenSource?: (slot: Slot) => boolean;
		onopensource?: (slot: Slot, path: string, line: number | null) => void;
	}

	let {
		assignmentId,
		block,
		answers,
		context,
		apps,
		observations,
		verdicts,
		verdictSaver,
		readOnly = false,
		consoleAvailable = false,
		onchange,
		onobservationsaved,
		onobservationdeleted,
		onverdict,
		canOpenSource,
		onopensource,
	}: Props = $props();

	const uid = $props.id();

	const OUTCOMES: ChoiceOption[] = [
		{ value: 'vulnerable', label: 'Vulnerable', tone: 'danger', title: 'You found this weakness in the app' },
		{ value: 'not_vulnerable', label: 'Not vulnerable', tone: 'success', title: 'You tested it and found no weakness' },
		{ value: 'not_tested', label: 'Not tested', tone: 'neutral', title: 'You did not (or could not) test it' },
	];

	const checklist = $derived(answers.checklist ?? {});
	const answered = $derived(block.checklist.filter((c) => checklist[c.id]?.outcome).length);
	const slotLabels = $derived(Object.fromEntries(apps.map((a) => [a.slot, a.label])));
	let openHelp = $state<Record<string, boolean>>({});
	let openNote = $state<Record<string, boolean>>({});

	function setOutcome(id: string, outcome: string) {
		const prev = checklist[id] ?? {};
		onchange({ ...answers, checklist: { ...checklist, [id]: { ...prev, outcome: outcome as ChecklistOutcome } } });
	}

	function setNote(id: string, note: string) {
		const prev = checklist[id];
		if (!prev?.outcome) return;
		onchange({ ...answers, checklist: { ...checklist, [id]: { ...prev, note } } });
	}
</script>

<div class="space-y-6">
	{#if consoleAvailable}
		<p class="flex items-start gap-2 rounded-md border border-border bg-surface-2 px-3 py-2 text-xs text-muted-foreground">
			<TerminalSquare class="mt-px size-4 shrink-0" aria-hidden="true" />
			<span>Use the <b class="font-medium text-foreground">HTTP console</b> next to the app to send raw requests (other users' ids, missing tokens, unexpected input). Every request is logged for the study.</span>
		</p>
	{/if}

	{#if block.checklist.length > 0}
		<section class="space-y-3">
			<SectionHeading size="sm" title="Checklist" count={`${answered}/${block.checklist.length}`} />
			<ul class="space-y-2">
				{#each block.checklist as item, i (item.id)}
					{@const entry = checklist[item.id]}
					<li class="space-y-2 rounded-md border border-border bg-surface-1 px-3 py-2.5">
						<div class="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
							<div class="min-w-0">
								<p id="{uid}-i{i}" class="text-sm font-medium leading-snug">{item.label}</p>
								{#if item.category}<p class="text-[11px] text-muted-foreground">{item.category}</p>{/if}
							</div>
							{#if item.help}
								<button
									type="button"
									class="shrink-0 cursor-pointer text-xs text-primary hover:underline"
									aria-expanded={!!openHelp[item.id]}
									aria-controls="{uid}-help{i}"
									onclick={() => (openHelp[item.id] = !openHelp[item.id])}
								>
									{openHelp[item.id] ? 'Hide help' : 'How to test'}
								</button>
							{/if}
						</div>
						{#if item.help && openHelp[item.id]}
							<p id="{uid}-help{i}" class="whitespace-pre-line rounded bg-surface-2 px-2.5 py-2 text-xs leading-relaxed text-muted-foreground">
								{item.help}
							</p>
						{/if}
						<ChoiceGroup
							value={entry?.outcome ?? ''}
							options={OUTCOMES}
							size="sm"
							{readOnly}
							aria-labelledby="{uid}-i{i}"
							onchange={(v) => setOutcome(item.id, v)}
						/>
						{#if readOnly}
							{#if entry?.note}<p class="whitespace-pre-line break-words text-xs text-muted-foreground">Note: {entry.note}</p>{/if}
						{:else if entry?.outcome}
							{#if openNote[item.id] || entry.note}
								<Textarea
									rows={2}
									class="min-h-0 text-xs"
									value={entry.note ?? ''}
									maxlength={2000}
									aria-label="Note on {item.label}"
									placeholder="What did you try? (optional)"
									oninput={(e: Event) => setNote(item.id, (e.currentTarget as HTMLTextAreaElement).value)}
								/>
							{:else}
								<button type="button" class="cursor-pointer text-xs text-muted-foreground hover:text-foreground hover:underline" onclick={() => (openNote[item.id] = true)}>
									Add a note
								</button>
							{/if}
						{/if}
					</li>
				{/each}
			</ul>
		</section>
	{/if}

	{#if block.reports}
		<section class="space-y-3">
			<SectionHeading
				size="sm"
				title="Vulnerability reports"
				count={observations.filter((o) => o.kind === 'vulnerability' && o.block_id === block.id).length}
			/>
			<p class="text-xs text-muted-foreground">
				Report each weakness you found, with steps so others can confirm it. Severity is your own judgement.
			</p>
			{#each apps as app (app.slot)}
				{#if apps.length > 1}<p class="section-label">{app.label}</p>{/if}
				<ObservationList
					{assignmentId}
					kind="vulnerability"
					slot={app.slot}
					blockId={block.id}
					appLabel={app.label}
					observations={observations.filter((o) => o.kind === 'vulnerability' && o.block_id === block.id && o.app_slot === app.slot)}
					checklist={block.checklist}
					{readOnly}
					addLabel="Report a vulnerability"
					onsaved={onobservationsaved}
					ondeleted={onobservationdeleted}
				/>
			{/each}
		</section>
	{/if}

	{#if block.finding_review?.enabled}
		<section class="space-y-3">
			<SectionHeading size="sm" title="Review automated findings" />
			<FindingReview
				saver={verdictSaver}
				findingsBySlot={context?.findings ?? {}}
				{slotLabels}
				{verdicts}
				{readOnly}
				{canOpenSource}
				{onverdict}
				{onopensource}
			/>
		</section>
	{/if}
</div>
