<script lang="ts">
	/**
	 * ScenariosForm — usability scenarios: per scenario the task text, an
	 * optional start/stop timer (seconds are saved when the timer stops or the
	 * outcome is recorded), the outcome, the Single Ease Question (1..7) and a note.
	 *
	 * Props: `block` (ScenariosBlock), `answers`, `readOnly`, `onchange(next)`.
	 */
	import Play from '@lucide/svelte/icons/play';
	import Pause from '@lucide/svelte/icons/pause';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import type { ScenarioOutcome, ScenariosAnswers, ScenariosBlock } from '$lib/api/types/human-eval';
	import { Button } from '$lib/components/ui/button';
	import { Textarea } from '$lib/components/ui/textarea';
	import Markdown from '$lib/components/eval-portal/Markdown.svelte';
	import ChoiceGroup, { type ChoiceOption } from './ChoiceGroup.svelte';
	import ScaleInput from './ScaleInput.svelte';
	import { formatDuration } from './workbench';

	interface Props {
		block: ScenariosBlock;
		answers: ScenariosAnswers;
		readOnly?: boolean;
		onchange: (next: ScenariosAnswers) => void;
	}

	let { block, answers, readOnly = false, onchange }: Props = $props();
	const uid = $props.id();

	type Entry = ScenariosAnswers['scenarios'][string];

	const OUTCOMES: ChoiceOption[] = [
		{ value: 'success', label: 'Done', tone: 'success', title: 'You completed the task as described' },
		{ value: 'partial', label: 'Partly', tone: 'warning', title: 'You completed only part of it, or with a workaround' },
		{ value: 'failure', label: 'Failed', tone: 'danger', title: 'You tried but could not complete it' },
		{ value: 'not_attempted', label: 'Not attempted', tone: 'neutral', title: 'You did not try it' },
	];
	const SEQ = { min: 1, max: 7, min_label: 'Very difficult', max_label: 'Very easy' };

	const given = $derived(answers.scenarios ?? {});
	/** scenario id → timer start (ms) while running. */
	let running = $state<Record<string, number>>({});
	let now = $state(Date.now());
	const anyRunning = $derived(Object.keys(running).length > 0);

	$effect(() => {
		if (!anyRunning) return;
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	function elapsed(id: string): number {
		const base = given[id]?.seconds ?? 0;
		const started = running[id];
		return started ? base + Math.max(0, (now - started) / 1000) : base;
	}

	function update(id: string, patch: Partial<Entry>) {
		const prev = (given[id] ?? {}) as Partial<Entry>;
		onchange({ ...answers, scenarios: { ...given, [id]: { ...prev, ...patch } as Entry } });
	}

	function start(id: string) {
		now = Date.now();
		running = { ...running, [id]: now };
	}

	function stoppedPatch(id: string): Partial<Entry> {
		if (!running[id]) return {};
		const seconds = Math.round(elapsed(id));
		const next = { ...running };
		delete next[id];
		running = next;
		return { seconds };
	}

	function stop(id: string) {
		update(id, stoppedPatch(id));
	}

	function reset(id: string) {
		const next = { ...running };
		delete next[id];
		running = next;
		update(id, { seconds: null });
	}

	function setOutcome(id: string, outcome: string) {
		const patch: Partial<Entry> = { ...stoppedPatch(id), outcome: outcome as ScenarioOutcome };
		if (outcome === 'not_attempted') patch.ease = null;
		update(id, patch);
	}
</script>

<ol class="space-y-4">
	{#each block.scenarios as scenario, i (scenario.id)}
		{@const entry = given[scenario.id]}
		{@const isRunning = !!running[scenario.id]}
		<li class="space-y-3 rounded-md border border-border bg-surface-1 px-3 py-3">
			<div>
				<p id="{uid}-s{i}" class="text-sm font-semibold leading-snug">
					<span class="mr-1 font-mono text-xs font-medium text-muted-foreground tabular-nums">{i + 1}.</span>{scenario.title}
				</p>
				{#if scenario.description}
					<Markdown source={scenario.description} class="mt-1 text-sm" />
				{/if}
				{#if scenario.success_criteria}
					<div class="mt-2 rounded bg-surface-2 px-2.5 py-2 text-xs">
						<p class="section-label">Done when</p>
						<Markdown source={scenario.success_criteria} />
					</div>
				{/if}
			</div>

			{#if block.timed}
				<div class="flex flex-wrap items-center gap-2">
					<span
						class="min-w-[4.5rem] rounded border border-border bg-card px-2 py-1 text-center font-mono text-sm tabular-nums"
						role="timer"
						aria-live="off"
						aria-label="Time on this scenario"
					>
						{formatDuration(elapsed(scenario.id))}
					</span>
					{#if !readOnly}
						{#if isRunning}
							<Button size="sm" variant="outline" onclick={() => stop(scenario.id)}>
								<Pause />
								Stop
							</Button>
						{:else}
							<Button size="sm" variant="outline" onclick={() => start(scenario.id)}>
								<Play />
								{entry?.seconds ? 'Resume' : 'Start'}
							</Button>
						{/if}
						{#if entry?.seconds || isRunning}
							<Button size="sm" variant="ghost" onclick={() => reset(scenario.id)} aria-label="Reset the timer">
								<RotateCcw />
							</Button>
						{/if}
						<span class="text-[11px] text-muted-foreground">
							{isRunning ? 'Timing… recording the outcome stops it.' : 'Start when you begin the task.'}
						</span>
					{/if}
				</div>
			{/if}

			<div class="space-y-1.5">
				<p class="text-xs font-medium text-muted-foreground">Outcome</p>
				<ChoiceGroup
					value={entry?.outcome ?? ''}
					options={OUTCOMES}
					size="sm"
					{readOnly}
					aria-labelledby="{uid}-s{i}"
					onchange={(v) => setOutcome(scenario.id, v)}
				/>
			</div>

			{#if block.ease_question && entry?.outcome && entry.outcome !== 'not_attempted'}
				<div class="space-y-1.5">
					<p id="{uid}-e{i}" class="text-xs font-medium">Overall, how difficult or easy was this task?</p>
					<ScaleInput
						scale={SEQ}
						value={entry.ease ?? undefined}
						{readOnly}
						aria-labelledby="{uid}-e{i}"
						onchange={(v) => update(scenario.id, { ease: v === 'na' ? null : v })}
					/>
				</div>
			{/if}

			{#if entry?.outcome}
				{#if readOnly}
					{#if entry.note}<p class="whitespace-pre-line break-words text-xs text-muted-foreground">Note: {entry.note}</p>{/if}
				{:else}
					<Textarea
						rows={2}
						class="min-h-0 text-xs"
						value={entry.note ?? ''}
						maxlength={2000}
						aria-label="Note on {scenario.title}"
						placeholder="Anything that got in your way? (optional)"
						oninput={(e: Event) => update(scenario.id, { note: (e.currentTarget as HTMLTextAreaElement).value })}
					/>
				{/if}
			{/if}
		</li>
	{/each}
</ol>
