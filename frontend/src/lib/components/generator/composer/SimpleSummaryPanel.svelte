<script lang="ts">
	/**
	 * SimpleSummaryPanel — Run summary for template scaffolding and single
	 * prompt: a checklist until the form is complete, then the client tally
	 * (`children`), the effective sampling params from `/jobs/llm-preview/`,
	 * client-side issues and the Start button (two-column layout only).
	 *
	 * Props: `checklist`, `ready`, `llm` (tracker), `llmNote` (copy under the
	 * params), `issues` + `actions`, Start state (`canStart`, `blockedReason`,
	 * `submitting`, `submitError`, `startLabel`, `onstart`), `children` (tally).
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EffectiveParams from './EffectiveParams.svelte';
	import SummaryChecklist from './SummaryChecklist.svelte';
	import SummaryIssues from './SummaryIssues.svelte';
	import { BLOCKED_START_CLASS, type ChecklistItem, type IssueAction, type SummaryIssue } from './issues';
	import type { LLMPreviewTracker } from './llm-preview.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';

	interface Props {
		checklist: ChecklistItem[];
		ready: boolean;
		llm: LLMPreviewTracker;
		llmNote?: string;
		issues: SummaryIssue[];
		actions?: Record<string, IssueAction>;
		canStart: boolean;
		blockedReason: string;
		submitting: boolean;
		submitError: string;
		startLabel: string;
		onstart: () => void;
		children?: Snippet;
	}

	let {
		checklist,
		ready,
		llm,
		llmNote = '',
		issues,
		actions = {},
		canStart,
		blockedReason,
		submitting,
		submitError,
		startLabel,
		onstart,
		children,
	}: Props = $props();
</script>

<Card.Root class="scroll-mt-20 gap-3" id="run-summary" aria-labelledby="run-summary-title">
	<Card.Header>
		<Card.Title><h2 id="run-summary-title">Run summary</h2></Card.Title>
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if !ready}
			<SummaryChecklist items={checklist} />
		{:else}
			{@render children?.()}
		{/if}

		<div class="space-y-1.5">
			<div class="flex items-center justify-between gap-2">
				<p class="section-label">Effective sampling</p>
				{#if llm.loading}
					<span class="flex items-center gap-1.5 text-[11px] text-muted-foreground" role="status">
						<LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" aria-hidden="true" />Updating…
					</span>
				{/if}
			</div>
			{#if llm.error && !llm.result}
				<ErrorAlert message={llm.error} onretry={() => llm.retry()} />
			{:else if llm.result}
				<div class={cn('transition-opacity duration-200', llm.stale && 'opacity-60')}>
					<EffectiveParams effective={llm.result.effective} sources={llm.result.sources} />
				</div>
				{#if llm.error}
					<p class="text-[11px] text-danger-fg">
						{llm.error}
						<button type="button" class="cursor-pointer font-medium underline-offset-2 hover:underline" onclick={() => llm.retry()}>Retry</button>
					</p>
				{/if}
			{:else}
				<p class="text-xs text-muted-foreground">Resolving…</p>
			{/if}
			{#if llmNote}<p class="text-[11px] text-muted-foreground">{llmNote}</p>{/if}
		</div>

		<SummaryIssues {issues} {actions} />

		{#if submitError}
			<ErrorAlert title="Couldn’t start" message={submitError} />
		{/if}

		<div class="hidden space-y-1.5 border-t border-border/60 pt-3 @4xl/composer:block">
			<Button class={cn('w-full', !canStart && !submitting && BLOCKED_START_CLASS)} disabled={!canStart || submitting} onclick={onstart}>
				{#if submitting}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}
				{startLabel}
			</Button>
			{#if !canStart && blockedReason}
				<p class="text-center text-[11px] text-muted-foreground">{blockedReason}</p>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
