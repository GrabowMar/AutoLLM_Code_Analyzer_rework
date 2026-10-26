<script lang="ts">
	/**
	 * SimpleSummaryPanel — Run summary for template scaffolding and single
	 * prompt: the run formula on top (empty factors link to their step), the
	 * client details once the form is complete (`children`), the effective sampling params from `/jobs/llm-preview/`,
	 * client-side issues and the Start button (two-column layout only).
	 *
	 * Props: `factors` + `total` (RunFormula), `ready`, `llm` (tracker), `llmNote` (copy under the
	 * params), `issues` + `actions`, Start state (`canStart`, `blockedReason`,
	 * `submitting`, `submitError`, `startLabel`, `onstart`), `children` (tally).
	 */
	import type { Snippet } from 'svelte';
	import { cn } from '$lib/utils';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EffectiveParams from './EffectiveParams.svelte';
	import RunFormula, { type RunFactor } from './RunFormula.svelte';
	import SummaryIssues from './SummaryIssues.svelte';
	import { type IssueAction, type SummaryIssue } from './issues';
	import type { LLMPreviewTracker } from './llm-preview.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';

	interface Props {
		factors: RunFactor[];
		total: number | null;
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
		factors,
		total,
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
		<RunFormula {factors} {total} />
		{#if ready}{@render children?.()}{/if}

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
			<Button class="w-full" disabled={!canStart || submitting} aria-busy={submitting} onclick={onstart}>
				{#if submitting}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}
				{startLabel}
			</Button>
			{#if !canStart && blockedReason}
				<p class="text-center text-[11px] text-muted-foreground">{blockedReason}</p>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
