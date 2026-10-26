<script lang="ts">
	/**
	 * CoverageBanner — what a run's numbers do and do not cover.
	 *
	 * Shown on every run: while it runs (partial counts), after it finished
	 * with tools that did not succeed (lower bounds, NA outcomes), when it
	 * failed or was cancelled, and for sample-quality flags (truncated
	 * generation, empty output, validity exclusion, resolver warnings). Only
	 * a run whose every tool succeeded or was not applicable gets the green
	 * "complete" line: zero findings never reads as clean otherwise.
	 *
	 * Props: `run` (detail), `ontools?()` (jump to the tool status table).
	 */
	import type { AnalysisRunDetail } from '$lib/api/analysis';
	import { isMeasurement, isRunActive } from '$lib/api/analysis';
	import { toneClasses, type Tone } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import {
		failureReasonLabel,
		fmtAgo,
		phaseLabel,
		toolProblems,
		toolProgress,
		type ToolProblem
	} from './run-utils';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import Info from '@lucide/svelte/icons/info';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import Ban from '@lucide/svelte/icons/ban';

	interface Props {
		run: AnalysisRunDetail;
		ontools?: () => void;
		class?: string;
	}

	let { run, ontools, class: className }: Props = $props();

	type Kind =
		| 'active'
		| 'stale'
		| 'cancelling'
		| 'failed'
		| 'cancelled'
		| 'incomplete'
		| 'optional'
		| 'complete';

	const problems = $derived(toolProblems(run.results));
	const requiredProblems = $derived(problems.filter((p) => p.required));
	const optionalProblems = $derived(problems.filter((p) => !p.required));
	const progress = $derived(toolProgress(run));
	const notApplicable = $derived(
		run.results.filter((r) => r.status === 'not_applicable').map((r) => r.tool_slug)
	);
	const succeededCount = $derived(run.results.filter((r) => r.status === 'succeeded').length);

	const kind = $derived.by((): Kind => {
		if (isRunActive(run.status)) {
			if (run.cancel_requested) return 'cancelling';
			if (run.stale) return 'stale';
			return 'active';
		}
		if (run.status === 'failed') return 'failed';
		if (run.status === 'cancelled') return 'cancelled';
		if (!run.is_complete) return 'incomplete';
		if (optionalProblems.length > 0) return 'optional';
		return 'complete';
	});

	const tone = $derived.by((): Tone => {
		switch (kind) {
			case 'active':
				return 'info';
			case 'cancelling':
				return 'neutral';
			case 'failed':
				return 'danger';
			case 'complete':
				return flags.length > 0 ? 'warning' : 'success';
			case 'optional':
				return 'info';
			default:
				return 'warning';
		}
	});

	const title = $derived.by(() => {
		switch (kind) {
			case 'active':
				return run.status === 'queued'
					? 'Queued — nothing has been measured yet'
					: `Analysis in progress${run.phase ? ` (${phaseLabel(run.phase)})` : ''}`;
			case 'stale':
				return 'No heartbeat from the worker';
			case 'cancelling':
				return 'Cancelling';
			case 'failed':
				return 'The run failed — it has no usable measurement';
			case 'cancelled':
				return succeededCount > 0
					? 'Cancelled — results cover only the tools that finished'
					: 'Cancelled before any tool finished — nothing was measured';
			case 'incomplete':
				return 'Incomplete — required tools did not succeed';
			case 'optional':
				return 'Complete, but optional tools did not succeed';
			default:
				return 'Complete — every tool succeeded or was not applicable';
		}
	});

	const description = $derived.by(() => {
		switch (kind) {
			case 'active':
				return `${progress.done} of ${progress.total} tools finished. Counts on this page are partial until the run finishes; they refresh automatically.`;
			case 'stale':
				return `The last heartbeat was ${fmtAgo(run.heartbeat_at)}. Stale runs are recovered automatically (requeued; attempt ${run.attempt}).`;
			case 'cancelling':
				return 'The current tool is stopped and the remaining tools are cancelled. Tools that already finished keep their results.';
			case 'failed':
				return run.error_message || 'No tool produced data.';
			case 'cancelled':
				return succeededCount > 0
					? 'Totals are a lower bound and outcomes that need the missing tools are not available.'
					: 'No tool produced data, so there are no findings to report — this is not a clean result.';
			case 'incomplete':
				return 'Static totals are a lower bound and per-KLOC outcomes that depend on these tools are not available (—). Zero findings from the remaining tools does not mean clean.';
			case 'optional':
				return 'Every required tool succeeded, so the measurement is complete; the findings these optional tools would have reported are missing.';
			default:
				return notApplicable.length
					? `Not applicable (no files in scope): ${notApplicable.join(', ')}.`
					: '';
		}
	});

	const listed = $derived(
		kind === 'optional'
			? optionalProblems
			: kind === 'active' || kind === 'stale' || kind === 'cancelling'
				? []
				: problems
	);
	let expanded = $state(false);
	const LIST_LIMIT = 5;
	const visible = $derived(expanded ? listed : listed.slice(0, LIST_LIMIT));

	// Sample-quality flags travel with the run whatever its status.
	const flags = $derived.by(() => {
		const out: string[] = [];
		const meta = run.target_meta ?? {};
		if (meta.empty_output) out.push('The generation wrote no source code (empty output).');
		if (meta.truncated) {
			const reasons = (meta.truncation_reasons ?? []).join(', ');
			out.push(`The generation was truncated${reasons ? ` (${reasons})` : ''}.`);
		}
		if (meta.validity?.exclude)
			out.push('The sample is flagged for exclusion by its validity check.');
		const m = run.measurement;
		if (isMeasurement(m)) {
			const share = m.parse_coverage?.share_unparseable_sloc;
			const files = m.parse_coverage?.unparseable_files ?? [];
			if (files.length) {
				out.push(
					`${files.length} file${files.length === 1 ? '' : 's'} could not be parsed by every tool${
						share != null ? ` (${(share * 100).toFixed(1)}% of measured SLOC)` : ''
					}.`
				);
			}
		}
		for (const warning of run.manifest?.warnings ?? []) out.push(warning);
		return out;
	});

	function problemText(p: ToolProblem): string {
		const why = failureReasonLabel(p.reason) || p.status.replace(/_/g, ' ');
		return why;
	}

	const Icon = $derived(
		kind === 'active'
			? LoaderCircle
			: kind === 'complete'
				? flags.length > 0
					? TriangleAlert
					: CircleCheck
				: kind === 'failed'
					? CircleX
					: kind === 'optional'
						? Info
						: kind === 'cancelling' || kind === 'cancelled'
							? Ban
							: TriangleAlert
	);
</script>

<section
	class={cn('rounded-md border px-3 py-2.5 sm:px-4', toneClasses[tone], className)}
	aria-label="Run coverage"
	role={kind === 'complete' ? 'status' : 'alert'}
>
	<div class="flex items-start gap-2.5">
		<Icon
			class={cn(
				'mt-0.5 size-4 shrink-0',
				kind === 'active' && 'animate-spin motion-reduce:animate-none'
			)}
			aria-hidden="true"
		/>
		<div class="min-w-0 flex-1 space-y-1.5">
			<p class="text-sm font-medium">{title}</p>
			{#if description}
				<p class="text-xs text-muted-foreground">{description}</p>
			{/if}

			{#if listed.length > 0}
				<ul class="space-y-1 text-xs">
					{#each visible as p (p.slug)}
						<li class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
							<span class="font-mono font-medium text-foreground">{p.slug}</span>
							<span class="font-medium">{problemText(p)}</span>
							<span class="rounded border border-current/30 px-1 text-[11px] leading-4"
								>{p.required ? 'required' : 'optional'}</span
							>
							{#if p.message && p.message.toLowerCase() !== problemText(p)}
								<span class="min-w-0 break-words text-muted-foreground">{p.message}</span>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}

			{#if flags.length > 0}
				<ul class="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
					{#each flags as flag, i (i)}
						<li>{flag}</li>
					{/each}
				</ul>
			{/if}

			{#if listed.length > LIST_LIMIT || (ontools && kind !== 'complete')}
				<div class="flex flex-wrap gap-x-4 gap-y-1">
					{#if listed.length > LIST_LIMIT}
						<button
							type="button"
							class="cursor-pointer text-xs font-medium underline underline-offset-2"
							onclick={() => (expanded = !expanded)}
							aria-expanded={expanded}
						>
							{expanded ? 'Show fewer' : `Show all ${listed.length} tools`}
						</button>
					{/if}
					{#if ontools && kind !== 'complete'}
						<button
							type="button"
							class="cursor-pointer text-xs font-medium underline underline-offset-2"
							onclick={ontools}
						>
							Tool status and diagnostics
						</button>
					{/if}
				</div>
			{/if}
		</div>
	</div>
</section>
