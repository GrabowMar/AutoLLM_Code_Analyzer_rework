<script lang="ts">
	/**
	 * SelftestCard — the canary self-test: latest verdict per static tool on the
	 * current image, with the expected rules a tool missed and the findings it
	 * raised on the clean fixture; starts a new self-test and shows its progress.
	 *
	 * Props: `selftest` (`SelftestLatest` or null while loading), `error`,
	 * `tools` (catalog, for names and which tools have expectations),
	 * `active` (the self-test run in flight, if any), `starting`, `currentTag`
	 * (the image tag the catalog resolves now), `canStart`, `onstart()`, `onrefresh()`.
	 */
	import type {
		AnalysisRunListItem,
		AnalysisTool,
		CanaryToolVerdict,
		SelftestLatest
	} from '$lib/api/analysis';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { toolIcon } from '$lib/components/analysis/profile/profile-utils';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		selftest: SelftestLatest | null;
		error?: string;
		tools: AnalysisTool[];
		active: AnalysisRunListItem | null;
		starting?: boolean;
		canStart?: boolean;
		startBlockedReason?: string;
		onstart?: () => void;
		onrefresh?: () => void;
	}

	let {
		selftest,
		error = '',
		tools,
		active,
		starting = false,
		canStart = true,
		startBlockedReason = '',
		onstart,
		onrefresh
	}: Props = $props();

	// Tools with canary expectations (the AI reviewer has none).
	const testable = $derived(tools.filter((t) => t.kind !== 'ai'));

	interface Row {
		tool: AnalysisTool;
		verdict: CanaryToolVerdict | null;
	}

	const rows = $derived<Row[]>(
		testable.map((tool) => ({ tool, verdict: selftest?.per_tool[tool.slug] ?? null }))
	);
	const counts = $derived({
		passed: rows.filter((r) => r.verdict?.verdict === 'passed').length,
		failed: rows.filter((r) => r.verdict?.verdict === 'failed').length,
		unverified: rows.filter((r) => r.verdict?.verdict === 'unverified').length,
		untested: rows.filter((r) => !r.verdict).length
	});

	const latest = $derived(selftest?.latest ?? null);
	const overall = $derived.by(() => {
		if (active) return { status: active.status, label: `self-test ${active.status}` };
		if (!selftest) return { status: 'pending', label: 'checking' };
		if (counts.failed > 0) return { status: 'canary_failed', label: `${counts.failed} failed` };
		if (counts.untested > 0 || counts.unverified > 0) {
			return {
				status: 'untested',
				label: `${counts.untested + counts.unverified} without evidence`
			};
		}
		return { status: 'passed', label: 'all passed' };
	});

	const progress = $derived.by(() => {
		if (!active) return null;
		const t = active.tools;
		const done = t.total - t.pending - t.running;
		return { done, total: t.total, pct: t.total ? Math.round((done / t.total) * 100) : 0 };
	});

	function verdictLabel(v: CanaryToolVerdict | null): { status: string; label: string } {
		if (!v) return { status: 'untested', label: 'untested' };
		if (v.verdict === 'passed') return { status: 'passed', label: 'passed' };
		if (v.verdict === 'failed') return { status: 'failed', label: 'failed' };
		return { status: 'unverified', label: 'unverified' };
	}

	function matched(v: CanaryToolVerdict): string {
		return `${v.expected.length - v.missing.length}/${v.expected.length}`;
	}

	function rule(entry: { rule: string; path?: string }): string {
		return entry.path ? `${entry.rule} in ${entry.path}` : entry.rule;
	}
</script>

<Card.Root class="flex flex-col">
	<Card.Header class="pb-3">
		<div class="flex flex-wrap items-start justify-between gap-2">
			<div class="min-w-0 space-y-1">
				<Card.Title class="flex items-center gap-2 text-base">
					<FlaskConical class="size-4 text-muted-foreground" aria-hidden="true" />Self-test
				</Card.Title>
				<Card.Description>
					Runs every static tool through the real pipeline on a vulnerable and a clean fixture app.
					A tool passes when it finds every expected rule and raises nothing counted on the clean
					app — zero findings alone never proves a tool works.
				</Card.Description>
			</div>
			<StatusBadge status={overall.status} label={overall.label} />
		</div>
	</Card.Header>
	<Card.Content class="flex-1 space-y-4 text-sm">
		{#if active && progress}
			<div
				class="space-y-1.5 rounded-md border border-info/30 bg-info/10 px-3 py-2.5"
				role="status"
			>
				<div class="flex flex-wrap items-center justify-between gap-2 text-xs">
					<span class="flex items-center gap-1.5 font-medium text-info-fg">
						<LoaderCircle
							class="size-3.5 animate-spin motion-reduce:animate-none"
							aria-hidden="true"
						/>
						Self-test {active.status} · {(active.phase || 'queued')
							.replace(':', ' ')
							.replace(/_/g, ' ')}
					</span>
					<a
						href="/analysis/{active.id}"
						class="rounded font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
						>Open run</a
					>
				</div>
				<div
					class="h-1.5 overflow-hidden rounded-full bg-muted"
					role="progressbar"
					aria-label="Self-test progress"
					aria-valuemin={0}
					aria-valuemax={progress.total}
					aria-valuenow={progress.done}
				>
					<div
						class="h-full rounded-full bg-info transition-[width] duration-300"
						style="width: {progress.pct}%"
					></div>
				</div>
				<p class="text-[11px] tabular-nums text-muted-foreground">
					{progress.done} of {progress.total} tools finished
				</p>
			</div>
		{/if}

		{#if error && !selftest}
			<ErrorAlert message={error} onretry={onrefresh} />
		{:else if !selftest}
			<LoadingState label="loading self-test" class="py-8" />
		{:else}
			<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
				{#if latest}
					<span>
						Last run {formatDate(latest.evaluated_at)} ·
						<a
							href="/analysis/{latest.run_id}"
							class="rounded font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
							>run details</a
						>
					</span>
					<span class="font-mono" title="Plan hash of the self-test run"
						>plan #{latest.plan_hash.slice(0, 10)}</span
					>
				{:else}
					<span>No finished self-test on this image yet.</span>
				{/if}
				{#if selftest.image_tag}<span class="break-all font-mono">{selftest.image_tag}</span>{/if}
			</div>

			{#if counts.failed > 0}
				<div
					class="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs"
					role="alert"
				>
					<TriangleAlert class="mt-0.5 size-3.5 shrink-0 text-danger-fg" aria-hidden="true" />
					<p>
						<span class="font-medium text-danger-fg"
							>{counts.failed} tool{counts.failed === 1 ? '' : 's'} failed the self-test.</span
						>
						<span class="text-muted-foreground"
							>Study preflights flag them; their results cannot be trusted on this image.</span
						>
					</p>
				</div>
			{:else if counts.untested > 0}
				<p class="text-xs text-muted-foreground">
					{counts.untested} tool{counts.untested === 1 ? ' has' : 's have'} no self-test evidence on
					this image — not a pass. Run the self-test before a study.
				</p>
			{/if}

			<div class="table-scroll-wrapper rounded-md border border-border">
				<table class="text-xs">
					<caption class="sr-only">Latest self-test verdict per tool</caption>
					<thead>
						<tr>
							<th scope="col">Tool</th>
							<th scope="col">Verdict</th>
							<th
								scope="col"
								class="text-right"
								title="Expected rules matched on the vulnerable fixture">Rules</th
							>
							<th scope="col" class="text-right" title="Counted findings on the clean fixture"
								>Clean</th
							>
							<th
								scope="col"
								class="hide-mobile text-right"
								title="Findings on the vulnerable fixture / minimum expected">Findings</th
							>
						</tr>
					</thead>
					<tbody>
						{#each rows as { tool, verdict } (tool.slug)}
							{@const Icon = toolIcon(tool.icon, tool.category)}
							{@const badge = verdictLabel(verdict)}
							{@const problems =
								verdict &&
								(verdict.missing.length > 0 ||
									verdict.unexpected_in_clean.length > 0 ||
									verdict.failure_reason)}
							<tr class={cn(problems && 'bg-destructive/5')}>
								<td>
									<span class="flex items-center gap-1.5 font-medium">
										<Icon
											class="size-3.5 shrink-0 text-muted-foreground"
											aria-hidden="true"
										/>{tool.name}
									</span>
								</td>
								<td>
									<StatusBadge status={badge.status} label={badge.label} />
									{#if verdict && verdict.status !== 'succeeded' && verdict.status !== 'not_applicable'}
										<span class="ml-1 text-[11px] text-danger-fg"
											>tool {verdict.status.replace(/_/g, ' ')}</span
										>
									{/if}
								</td>
								<td class="text-right font-mono tabular-nums">
									{#if verdict && verdict.has_expectations}{matched(verdict)}{:else}<span
											class="text-muted-foreground"
											title={verdict
												? 'No expected rules for this tool'
												: 'Not tested on this image'}>—</span
										>{/if}
								</td>
								<td class="text-right font-mono tabular-nums">
									{#if verdict}
										<span
											class={verdict.unexpected_in_clean.length > 0
												? 'font-semibold text-danger-fg'
												: ''}
										>
											{verdict.unexpected_in_clean.length}
										</span>
									{:else}<span class="text-muted-foreground" title="Not tested on this image"
											>—</span
										>{/if}
								</td>
								<td class="hide-mobile text-right font-mono tabular-nums">
									{#if verdict}
										{verdict.positive_findings}{#if verdict.min_findings != null}<span
												class="text-muted-foreground">{` / ≥${verdict.min_findings}`}</span
											>{/if}
									{:else}<span class="text-muted-foreground" title="Not tested on this image"
											>—</span
										>{/if}
								</td>
							</tr>
							{#if verdict && problems}
								<tr class="bg-destructive/5">
									<td colspan="5" class="pt-0">
										<div class="space-y-1 pl-5 text-[11px] leading-4">
											{#if verdict.failure_reason}
												<p>
													<span class="font-medium text-danger-fg">Failure:</span>
													{verdict.failure_reason.replace(/_/g, ' ')}
												</p>
											{/if}
											{#if verdict.missing.length > 0}
												<p>
													<span class="font-medium text-danger-fg">Missed:</span>
													<span class="font-mono">{verdict.missing.map(rule).join(', ')}</span>
												</p>
											{/if}
											{#if verdict.unexpected_in_clean.length > 0}
												<p>
													<span class="font-medium text-danger-fg">On the clean app:</span>
													<span class="font-mono"
														>{verdict.unexpected_in_clean
															.map((u) => `${u.rule} (${u.category}) in ${u.path}`)
															.join(', ')}</span
													>
												</p>
											{/if}
										</div>
									</td>
								</tr>
							{/if}
						{/each}
					</tbody>
				</table>
			</div>
			<p class="text-[11px] text-muted-foreground">
				The AI reviewer has no canary: its reliability is measured in every run (replicate
				agreement, grounding rate).
			</p>
		{/if}
	</Card.Content>
	<Card.Footer class="flex flex-wrap items-center justify-end gap-2 border-t border-border pt-4">
		{#if startBlockedReason && !active}
			<span class="mr-auto text-[11px] text-muted-foreground">{startBlockedReason}</span>
		{/if}
		<Button size="sm" onclick={onstart} disabled={!canStart || starting || !!active}>
			{#if starting || active}<LoaderCircle
					class="animate-spin motion-reduce:animate-none"
				/>{:else}<Play />{/if}
			{active ? 'Self-test running…' : 'Run self-test'}
		</Button>
	</Card.Footer>
</Card.Root>
