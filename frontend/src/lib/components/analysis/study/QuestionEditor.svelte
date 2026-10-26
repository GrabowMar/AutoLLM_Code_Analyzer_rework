<script lang="ts">
	/**
	 * QuestionEditor — study name, description and research questions
	 * (confirmatory questions are pre-registered and get the strict
	 * correction; exploratory ones the lenient one).
	 *
	 * Props: `name`, `description`, `questions` (all bindable; questions are
	 * replaced, never mutated), `errors` (server field errors: `name`,
	 * `research_questions.<i>.text`, ...), `outcomeOptions` (outcome key ->
	 * label: a confirmatory question can name the outcomes it tests),
	 * `disabled`, `onchange()`.
	 */
	import type { ResearchQuestionInput, ResearchQuestionKind } from '$lib/api/analysis';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import ArrowDown from '@lucide/svelte/icons/arrow-down';

	interface Props {
		name?: string;
		description?: string;
		questions?: ResearchQuestionInput[];
		errors?: Record<string, string>;
		/** Outcome key -> label (`/profiles/defaults/` outcomes). */
		outcomeOptions?: Record<string, string>;
		disabled?: boolean;
		onchange?: () => void;
	}

	let {
		name = $bindable(''),
		description = $bindable(''),
		questions = $bindable([]),
		errors = {},
		outcomeOptions = {},
		disabled = false,
		onchange
	}: Props = $props();

	const MAX = 30;
	const KINDS = [
		{
			value: 'confirmatory',
			label: 'Confirmatory',
			title:
				'Pre-registered hypothesis; p-values adjusted with the strict (confirmatory) correction.'
		},
		{
			value: 'exploratory',
			label: 'Exploratory',
			title: 'Hypothesis-generating; adjusted with the exploratory correction.'
		}
	];

	function update(i: number, patch: Partial<ResearchQuestionInput>) {
		questions = questions.map((q, k) => (k === i ? { ...q, ...patch } : q));
		onchange?.();
	}

	function add() {
		questions = [
			...questions,
			{
				text: '',
				kind: questions.length === 0 ? 'confirmatory' : 'exploratory'
			}
		];
		onchange?.();
	}

	function remove(i: number) {
		questions = questions.filter((_, k) => k !== i);
		onchange?.();
	}

	function move(i: number, delta: number) {
		const j = i + delta;
		if (j < 0 || j >= questions.length) return;
		const next = [...questions];
		[next[i], next[j]] = [next[j], next[i]];
		questions = next;
		onchange?.();
	}

	function setKind(i: number, kind: ResearchQuestionKind) {
		// Only confirmatory questions name outcomes (the server rejects them otherwise).
		update(i, kind === 'confirmatory' ? { kind } : { kind, outcomes: undefined });
	}

	function toggleOutcome(i: number, key: string, on: boolean) {
		const current = questions[i]?.outcomes ?? [];
		const next = on ? [...current, key] : current.filter((k) => k !== key);
		update(i, { outcomes: next.length ? [...new Set(next)] : undefined });
	}

	function questionId(q: ResearchQuestionInput, i: number): string {
		return q.id || `RQ${i + 1}`;
	}
</script>

<div class="space-y-5">
	<div class="grid gap-4 lg:grid-cols-2">
		<div class="space-y-1.5">
			<Label for="study-name">Name</Label>
			<Input
				id="study-name"
				bind:value={name}
				maxlength={200}
				placeholder="e.g. Security baseline: Qwen3 Coder vs GPT-5.4 Mini"
				aria-invalid={errors.name ? true : undefined}
				aria-describedby={errors.name ? 'study-name-error' : undefined}
				oninput={() => onchange?.()}
				required
				{disabled}
			/>
			{#if errors.name}<p id="study-name-error" class="text-[11px] text-danger-fg">
					{errors.name}
				</p>{/if}
		</div>
		<div class="space-y-1.5 lg:row-span-2">
			<Label for="study-description">Description</Label>
			<Textarea
				id="study-description"
				bind:value={description}
				rows={4}
				placeholder="What is analysed and why (goes into the protocol)."
				oninput={() => onchange?.()}
				{disabled}
			/>
		</div>
	</div>

	<section class="space-y-3" aria-labelledby="rq-heading">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<div>
				<h3 id="rq-heading" class="text-sm font-semibold">Research questions</h3>
				<p class="text-xs text-muted-foreground">
					Frozen into the protocol. Confirmatory questions get the strict multiple-comparison
					correction.
				</p>
			</div>
			{#if !disabled}
				<Button variant="outline" size="sm" onclick={add} disabled={questions.length >= MAX}>
					<Plus />Add question
				</Button>
			{/if}
		</div>
		{#if errors.research_questions}
			<p class="text-[11px] text-danger-fg">{errors.research_questions}</p>
		{/if}
		{#if questions.length === 0}
			<p
				class="rounded-md border border-dashed border-border px-3 py-4 text-center text-sm text-muted-foreground"
			>
				No research question yet. They are optional, but a protocol without them is hard to defend.
			</p>
		{:else}
			<ol class="space-y-3">
				{#each questions as q, i (i)}
					{@const base = `research_questions.${i}`}
					<li class="rounded-md border border-border bg-card p-3">
						<div class="flex flex-wrap items-center justify-between gap-2">
							<span class="mono-tag">{questionId(q, i)}</span>
							<div class="flex items-center gap-1">
								<Segmented
									size="sm"
									aria-label="Kind of {questionId(q, i)}"
									value={q.kind ?? 'exploratory'}
									items={KINDS}
									onValueChange={(v) => setKind(i, v as ResearchQuestionKind)}
									{disabled}
								/>
								{#if !disabled}
									<Button
										variant="ghost"
										size="icon"
										class="h-8 w-8"
										aria-label="Move {questionId(q, i)} up"
										disabled={i === 0}
										onclick={() => move(i, -1)}
									>
										<ArrowUp />
									</Button>
									<Button
										variant="ghost"
										size="icon"
										class="h-8 w-8"
										aria-label="Move {questionId(q, i)} down"
										disabled={i === questions.length - 1}
										onclick={() => move(i, 1)}
									>
										<ArrowDown />
									</Button>
									<Button
										variant="ghost"
										size="icon"
										class="h-8 w-8 text-danger-fg"
										aria-label="Remove {questionId(q, i)}"
										onclick={() => remove(i)}
									>
										<Trash2 />
									</Button>
								{/if}
							</div>
						</div>
						<div class="mt-3 grid gap-3 lg:grid-cols-2">
							<div class="space-y-1">
								<Label class="text-xs" for="rq-{i}-text">Question</Label>
								<Textarea
									id="rq-{i}-text"
									rows={2}
									value={q.text}
									placeholder="e.g. Do the models differ in static issue density?"
									aria-invalid={errors[`${base}.text`] ? true : undefined}
									oninput={(e: Event) =>
										update(i, {
											text: (e.currentTarget as HTMLTextAreaElement).value
										})}
									{disabled}
								/>
								{#if errors[`${base}.text`]}<p class="text-[11px] text-danger-fg">
										{errors[`${base}.text`]}
									</p>{/if}
							</div>
							<div class="space-y-1">
								<Label class="text-xs" for="rq-{i}-hypothesis"
									>Hypothesis <span class="text-muted-foreground">(optional)</span></Label
								>
								<Textarea
									id="rq-{i}-hypothesis"
									rows={2}
									value={q.hypothesis ?? ''}
									placeholder="e.g. Model A writes fewer issues per KLOC."
									oninput={(e: Event) =>
										update(i, {
											hypothesis: (e.currentTarget as HTMLTextAreaElement).value
										})}
									{disabled}
								/>
							</div>
						</div>
						{#if (q.kind ?? 'exploratory') === 'confirmatory' && Object.keys(outcomeOptions).length > 0}
							<fieldset class="mt-3 space-y-1.5">
								<legend class="text-xs font-medium">
									Tested outcomes <span class="font-normal text-muted-foreground"
										>(optional: none = the primary outcome)</span
									>
								</legend>
								<div class="flex flex-wrap gap-x-4 gap-y-1.5">
									{#each Object.entries(outcomeOptions) as [key, label] (key)}
										<label class="flex items-center gap-1.5 text-xs">
											<Checkbox
												checked={q.outcomes?.includes(key) ?? false}
												onCheckedChange={(v) => toggleOutcome(i, key, v === true)}
												{disabled}
											/>
											{label}
										</label>
									{/each}
								</div>
								<p class="text-[11px] text-muted-foreground">
									They join the confirmatory family (strict correction). Each must be analysed by
									the design (Method → outcomes).
								</p>
							</fieldset>
						{/if}
						{#each Object.entries(errors).filter(([k]) => k.startsWith(base) && k !== `${base}.text`) as [k, msg] (k)}
							<p class="mt-1 text-[11px] text-danger-fg">
								{k.slice(base.length + 1)}: {msg}
							</p>
						{/each}
					</li>
				{/each}
			</ol>
		{/if}
	</section>
</div>
