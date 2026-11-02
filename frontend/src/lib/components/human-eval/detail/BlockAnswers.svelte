<script lang="ts">
	/**
	 * BlockAnswers — one design block's saved answers for one assignment,
	 * read-only, using the assignment's frozen context for what the participant
	 * saw (requirement lists, findings under review, instrument questions):
	 *
	 * - rating: every criterion's value with its scale anchor (or N/A), comment;
	 * - requirements: per slot, every item's verdict and note;
	 * - security: checklist outcomes, finding verdicts (the tool's own level,
	 *   "unrated" when it printed none — never a normalized severity) and the
	 *   block's vulnerability reports;
	 * - scenarios: outcome, seconds, ease (SEQ) and note;
	 * - questionnaire: every question's answer (option labels);
	 * - bugs: "none found" per slot and the bug reports;
	 * - pairwise: every criterion as chosen, naming the app on that side.
	 * Missing answers show "—" (drafts can be partial). Text renders as text.
	 *
	 * Props: `block`, `answers`, `context` (BlockContext), `apps` (slot, label),
	 * `observations` (this block's), `verdicts`, `updatedAt`.
	 */
	import LevelBadge from '$lib/components/analysis/LevelBadge.svelte';
	import type {
		Block,
		BlockAnswers,
		BlockContext,
		BugsAnswers,
		FindingVerdictRecord,
		Observation,
		PairwiseAnswers,
		QuestionnaireAnswers,
		RatingAnswers,
		RequirementsAnswers,
		ReviewFinding,
		ScenariosAnswers,
		SecurityAnswers,
	} from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import {
		CHECKLIST_OUTCOME_LABELS,
		FINDING_VERDICT_LABELS,
		INSTRUMENT_LABELS,
		REQUIREMENT_VERDICT_LABELS,
		SCENARIO_OUTCOME_LABELS,
		formatSeconds,
		humanize,
		pairwiseValueText,
		questionAnswerText,
		ratingValueText,
		safeHttpUrl,
		scaleRangeText,
	} from '../format';
	import ObservationCard from './ObservationCard.svelte';

	interface Props {
		block: Block;
		answers: BlockAnswers | undefined;
		context: BlockContext | undefined;
		apps: { slot: string; label: string }[];
		observations: Observation[];
		verdicts: FindingVerdictRecord[];
		updatedAt?: string | null;
	}

	let { block, answers, context, apps, observations, verdicts, updatedAt = null }: Props = $props();

	const appLabel = (slot: string) => apps.find((a) => a.slot === slot)?.label ?? '';
	const slotTitle = (slot: string) => (slot === 'single' ? appLabel(slot) || 'App' : `${slot} · ${appLabel(slot)}`);
	const verdictByKey = $derived(new Map(verdicts.map((v) => [v.finding_key, v])));

	// Partial-safe views of the answers per type.
	const rating = $derived((answers ?? {}) as Partial<RatingAnswers>);
	const reqs = $derived((answers ?? {}) as Partial<RequirementsAnswers>);
	const sec = $derived((answers ?? {}) as Partial<SecurityAnswers>);
	const scen = $derived((answers ?? {}) as Partial<ScenariosAnswers>);
	const quest = $derived((answers ?? {}) as Partial<QuestionnaireAnswers>);
	const bugs = $derived((answers ?? {}) as Partial<BugsAnswers>);
	const pair = $derived((answers ?? {}) as Partial<PairwiseAnswers>);

	const VERDICT_TONE: Record<string, string> = {
		met: 'text-success-fg',
		partial: 'text-warning-fg',
		not_met: 'text-danger-fg',
		blocked: 'text-danger-fg',
		cannot_test: 'text-muted-foreground',
		vulnerable: 'text-danger-fg',
		not_vulnerable: 'text-success-fg',
		not_tested: 'text-muted-foreground',
		success: 'text-success-fg',
		failure: 'text-danger-fg',
		not_attempted: 'text-muted-foreground',
		tp: 'text-foreground',
		fp: 'text-foreground',
		unsure: 'text-muted-foreground',
	};

	function findingLocation(f: ReviewFinding): string {
		if (f.file_path) return f.line != null ? `${f.file_path}:${f.line}` : f.file_path;
		return [f.method, f.url].filter(Boolean).join(' ');
	}

	const checklistLabel = (id: string) =>
		block.type === 'security' ? (block.checklist.find((c) => c.id === id)?.label ?? id) : id;
</script>

{#snippet none()}<span class="text-muted-foreground">—</span>{/snippet}

{#snippet noteText(note: string | undefined)}
	{#if note}<p class="mt-0.5 whitespace-pre-wrap break-words text-xs text-muted-foreground">{note}</p>{/if}
{/snippet}

<div class="space-y-3 text-sm">
	{#if updatedAt}<p class="text-[11px] text-muted-foreground">Last saved {formatDateTime(updatedAt)}</p>{/if}
	{#if !answers}
		<p class="text-sm text-muted-foreground">No answers saved for this block.</p>
	{/if}

	{#if block.type === 'rating'}
		<table class="text-sm">
			<thead><tr><th>Criterion</th><th>Answer</th><th class="num">Scale</th></tr></thead>
			<tbody>
				{#each block.criteria as c (c.id)}
					{@const v = rating.criteria?.[c.id]}
					<tr>
						<td>{c.label}{#if c.required}<span class="text-muted-foreground" title="required">*</span>{/if}</td>
						<td class={cn(v == null && 'text-muted-foreground')}>{ratingValueText(v, c.scale)}</td>
						<td class="num text-xs text-muted-foreground">{scaleRangeText(c.scale)}</td>
					</tr>
				{/each}
			</tbody>
		</table>
		{#if block.comment !== 'off'}
			<div>
				<p class="section-label">Comment</p>
				{#if rating.comment}<p class="whitespace-pre-wrap break-words">{rating.comment}</p>{:else}{@render none()}{/if}
			</div>
		{/if}
	{:else if block.type === 'requirements'}
		{@const slots = Object.keys(context?.requirements ?? {})}
		{#if slots.length === 0}
			<p class="text-muted-foreground">No requirement list was recorded for this assignment.</p>
		{/if}
		{#each slots as slot (slot)}
			{@const items = context?.requirements?.[slot] ?? []}
			<div class="space-y-1">
				{#if slots.length > 1 || slot !== 'single'}<p class="section-label">{slotTitle(slot)}</p>{/if}
				<ul class="divide-y divide-border/60 rounded-md border border-border">
					{#each items as item (item.id)}
						{@const a = reqs.items?.[item.id]}
						<li class="flex flex-wrap items-start justify-between gap-x-4 gap-y-0.5 px-3 py-1.5">
							<div class="min-w-0 flex-1">
								<p class="break-words">{item.text}</p>
								<p class="font-mono text-[11px] text-muted-foreground">{item.source}</p>
								{@render noteText(a?.note)}
							</div>
							<span class={cn('shrink-0 text-xs font-medium', a ? VERDICT_TONE[a.verdict] : 'text-muted-foreground')}>
								{a ? (REQUIREMENT_VERDICT_LABELS[a.verdict] ?? humanize(a.verdict)) : '—'}
							</span>
						</li>
					{/each}
				</ul>
			</div>
		{/each}
	{:else if block.type === 'security'}
		{#if block.checklist.length > 0}
			<div class="space-y-1">
				<p class="section-label">Checklist</p>
				<ul class="divide-y divide-border/60 rounded-md border border-border">
					{#each block.checklist as item (item.id)}
						{@const a = sec.checklist?.[item.id]}
						<li class="flex flex-wrap items-start justify-between gap-x-4 gap-y-0.5 px-3 py-1.5">
							<div class="min-w-0 flex-1">
								<p class="break-words">{item.label}</p>
								{#if item.category}<p class="text-[11px] text-muted-foreground">{item.category}</p>{/if}
								{@render noteText(a?.note)}
							</div>
							<span class={cn('shrink-0 text-xs font-medium', a ? VERDICT_TONE[a.outcome] : 'text-muted-foreground')}>
								{a ? (CHECKLIST_OUTCOME_LABELS[a.outcome] ?? humanize(a.outcome)) : '—'}
							</span>
						</li>
					{/each}
				</ul>
			</div>
		{/if}
		{@const findingSlots = Object.keys(context?.findings ?? {})}
		{#if block.finding_review.enabled}
			<div class="space-y-1">
				<p class="section-label">Automated findings reviewed</p>
				{#if findingSlots.every((s) => (context?.findings?.[s] ?? []).length === 0)}
					<p class="text-muted-foreground">No findings were sampled for this app.</p>
				{/if}
				{#each findingSlots as slot (slot)}
					{@const findings = context?.findings?.[slot] ?? []}
					{#if findings.length > 0}
						{#if findingSlots.length > 1 || slot !== 'single'}<p class="text-xs text-muted-foreground">{slotTitle(slot)}</p>{/if}
						<ul class="divide-y divide-border/60 rounded-md border border-border">
							{#each findings as f (f.id)}
								{@const v = verdictByKey.get(f.id)}
								{@const docs = safeHttpUrl(f.docs_url)}
								<li class="space-y-1 px-3 py-2">
									<div class="flex flex-wrap items-start justify-between gap-2">
										<div class="min-w-0 flex-1">
											<p class="flex flex-wrap items-center gap-1.5">
												<span class="font-medium">{f.tool_name || f.tool}</span>
												<LevelBadge label={f.tool_severity} fallback="unrated" />
												{#if f.rule_id}<span class="font-mono text-[11px] text-muted-foreground">{f.rule_id}</span>{/if}
												{#if f.cwe}<span class="font-mono text-[11px] text-muted-foreground">{f.cwe}</span>{/if}
											</p>
											<p class="break-words">{f.title}</p>
											{#if findingLocation(f)}<p class="break-all font-mono text-[11px] text-muted-foreground">{findingLocation(f)}</p>{/if}
											{#if docs}<a href={docs} target="_blank" rel="noopener noreferrer" class="text-[11px] text-primary hover:underline">Rule documentation</a>{/if}
										</div>
										<span class={cn('shrink-0 text-xs font-medium', v ? VERDICT_TONE[v.verdict] : 'text-muted-foreground')}>
											{v ? (FINDING_VERDICT_LABELS[v.verdict] ?? v.verdict) : 'no verdict'}
										</span>
									</div>
									{#if f.code_snippet}
										<pre class="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-muted/30 p-2 font-mono text-[11px]">{f.code_snippet}</pre>
									{/if}
									{@render noteText(v?.note)}
								</li>
							{/each}
						</ul>
					{/if}
				{/each}
			</div>
		{/if}
		{#if block.reports}
			<div class="space-y-2">
				<p class="section-label">Vulnerability reports ({observations.length})</p>
				{#each observations as o (o.id)}
					<ObservationCard observation={o} appLabel={appLabel(o.app_slot)} {checklistLabel} />
				{:else}
					<p class="text-muted-foreground">None reported.</p>
				{/each}
			</div>
		{/if}
	{:else if block.type === 'scenarios'}
		<ul class="divide-y divide-border/60 rounded-md border border-border">
			{#each block.scenarios as s (s.id)}
				{@const a = scen.scenarios?.[s.id]}
				<li class="flex flex-wrap items-start justify-between gap-x-4 gap-y-0.5 px-3 py-1.5">
					<div class="min-w-0 flex-1">
						<p class="break-words font-medium">{s.title}</p>
						<p class="text-xs text-muted-foreground">
							{#if block.timed}time {formatSeconds(a?.seconds ?? null)}{/if}
							{#if block.ease_question}{block.timed ? ' · ' : ''}ease {a?.ease != null ? `${a.ease}/7` : '—'}{/if}
						</p>
						{@render noteText(a?.note)}
					</div>
					<span class={cn('shrink-0 text-xs font-medium', a ? VERDICT_TONE[a.outcome] : 'text-muted-foreground')}>
						{a ? (SCENARIO_OUTCOME_LABELS[a.outcome] ?? humanize(a.outcome)) : '—'}
					</span>
				</li>
			{/each}
		</ul>
	{:else if block.type === 'questionnaire'}
		{@const questions = block.instrument === 'custom' ? block.questions : (context?.questions ?? [])}
		<p class="text-xs text-muted-foreground">{INSTRUMENT_LABELS[block.instrument]}</p>
		{#if questions.length === 0}
			<p class="text-muted-foreground">The instrument’s questions were not recorded for this assignment.</p>
		{:else}
			<table class="text-sm">
				<thead><tr><th>Question</th><th>Answer</th></tr></thead>
				<tbody>
					{#each questions as q (q.id)}
						<tr>
							<td class="max-w-md break-words">{q.label}</td>
							<td class="break-words">{questionAnswerText(q, quest.answers?.[q.id])}</td>
						</tr>
					{/each}
				</tbody>
			</table>
		{/if}
	{:else if block.type === 'bugs'}
		{@const flags = bugs.none_found ?? {}}
		{#if Object.keys(flags).length > 0}
			<p class="text-xs">
				{#each Object.entries(flags) as [slot, value] (slot)}
					<span class="mr-3">{slotTitle(slot)}: {value ? 'confirmed no bugs found' : 'bugs expected'}</span>
				{/each}
			</p>
		{/if}
		<div class="space-y-2">
			<p class="section-label">Bug reports ({observations.length}{block.min_reports > 0 ? `, at least ${block.min_reports} per app required` : ''})</p>
			{#each observations as o (o.id)}
				<ObservationCard observation={o} appLabel={appLabel(o.app_slot)} />
			{:else}
				<p class="text-muted-foreground">None reported.</p>
			{/each}
		</div>
	{:else if block.type === 'pairwise'}
		<table class="text-sm">
			<thead><tr><th>Criterion</th><th>Preference (as displayed)</th></tr></thead>
			<tbody>
				{#each block.criteria as c (c.id)}
					<tr>
						<td>{c.label}</td>
						<td>{pairwiseValueText(pair.criteria?.[c.id], block.scale, apps)}</td>
					</tr>
				{/each}
				{#if block.overall}
					<tr>
						<td class="font-medium">Overall</td>
						<td class="font-medium">{pairwiseValueText(pair.overall, block.scale, apps)}</td>
					</tr>
				{/if}
			</tbody>
		</table>
		{#if block.rationale !== 'off'}
			<div>
				<p class="section-label">Rationale</p>
				{#if pair.rationale}<p class="whitespace-pre-wrap break-words">{pair.rationale}</p>{:else}{@render none()}{/if}
			</div>
		{/if}
	{/if}
</div>
