<script lang="ts">
	/**
	 * EvaluationCard — the platform evaluation of one app, for every mode
	 * (agent, scaffolding, custom): conformance result per check, build
	 * attempts and image size, build healing (what the platform repaired),
	 * the app's runtime exit, resolved packages and the tester's identity.
	 *
	 * Props: `job`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import ClipboardCheck from '@lucide/svelte/icons/clipboard-check';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleX from '@lucide/svelte/icons/circle-x';
	import { evaluateGenerationJob, type GenerationJob } from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import { fmtBytes, fmtSeconds, isPlainObject, shortHash } from './format';

	interface Props {
		job: GenerationJob;
	}

	let { job }: Props = $props();

	interface Check {
		category: string;
		name: string;
		passed: boolean;
		detail?: string;
	}

	const evaluation = $derived(
		isPlainObject(job.result_data?.evaluation) ? (job.result_data.evaluation as Record<string, any>) : null
	);
	const checks = $derived((Array.isArray(evaluation?.checks) ? evaluation.checks : []) as Check[]);
	const byCategory = $derived.by(() => {
		const groups = new Map<string, Check[]>();
		for (const c of checks) {
			const list = groups.get(c.category) ?? [];
			list.push(c);
			groups.set(c.category, list);
		}
		// Categories with failures first, then by name.
		return [...groups.entries()].sort(
			([a, x], [b, y]) => Number(y.some((c) => !c.passed)) - Number(x.some((c) => !c.passed)) || a.localeCompare(b)
		);
	});
	const build = $derived((isPlainObject(evaluation?.build) ? evaluation.build : null) as Record<string, any> | null);
	const attempts = $derived((Array.isArray(build?.attempts) ? build.attempts : []) as { ok?: boolean; seconds?: number; infra_suspected?: boolean }[]);
	const healing = $derived((isPlainObject(build?.healing) ? build.healing : null) as Record<string, any> | null);
	const resolved = $derived((Array.isArray(evaluation?.resolved_python_deps) ? evaluation.resolved_python_deps : []) as string[]);
	const hasSpec = $derived(job.app_requirement_id != null || isPlainObject(job.resolved_bundle?.app_requirement));
	const functional = $derived(isPlainObject(job.metrics?.functional) ? (job.metrics.functional as Record<string, any>) : null);
	// metrics.functional of older template runs is the generation-time smoke check, not this suite.
	const smokeOnly = $derived(!!functional && functional.source !== 'conformance');

	let openCategory = $state<string | null>(null);
	let showAppLog = $state(false);
	let showBuildLog = $state(false);
	let evaluating = $state(false);
	let evaluateMessage = $state('');

	async function evaluate() {
		evaluating = true;
		evaluateMessage = '';
		try {
			await evaluateGenerationJob(job.id);
			evaluateMessage = 'Evaluation queued — reload the page in a few minutes.';
		} catch (e) {
			evaluateMessage = formatApiError(e, 'Could not queue the evaluation');
		} finally {
			evaluating = false;
		}
	}
</script>

<Card.Root>
	<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-2 pb-2">
		<div class="space-y-1.5">
			<Card.Title class="flex items-center gap-1.5 text-sm"><ClipboardCheck class="size-4 text-muted-foreground" aria-hidden="true" />Platform evaluation</Card.Title>
			<Card.Description>Built with the stack's Dockerfile, run without network, graded by the hidden conformance suite.</Card.Description>
		</div>
		{#if evaluation}
			<div class="flex items-center gap-1.5">
				<StatusBadge status={evaluation.status} />
				{#if evaluation.summary?.total}
					<span class="font-mono text-xs tabular-nums">
						{evaluation.summary.passed}/{evaluation.summary.total} · {Math.round((evaluation.summary.pass_rate ?? 0) * 100)}%
					</span>
				{/if}
			</div>
		{/if}
	</Card.Header>
	<Card.Content class="space-y-4 text-sm">
		{#if !evaluation}
			{#if hasSpec}
				<EmptyState compact icon={ClipboardCheck} title="Not evaluated yet" description={job.status === 'completed' ? 'Build and grade this app against its spec’s conformance suite.' : job.status === 'failed' || job.status === 'cancelled' ? `Not evaluated: generation ${job.status === 'failed' ? 'failed' : 'was cancelled'}, so there is no app to grade.` : 'Evaluation runs once generation has finished.'}>
					{#snippet action()}
						{#if job.status === 'completed'}
							<Button variant="outline" size="sm" onclick={evaluate} disabled={evaluating}><RefreshCw class="h-3.5 w-3.5" />Evaluate</Button>
						{/if}
					{/snippet}
				</EmptyState>
			{:else}
				<p class="text-xs text-muted-foreground">Free-form task without an app spec: there is no conformance suite to grade it against.</p>
			{/if}
			{#if smokeOnly && functional}
				<p class="text-[11px] text-muted-foreground">
					Generation-time smoke check (not the conformance suite): {functional.endpoints_ok ?? '—'}/{functional.endpoints_checked ?? '—'} endpoints responded.
				</p>
			{/if}
		{:else}
			{#if evaluation.error}
				<p class="text-xs text-danger-fg">{evaluation.error}</p>
			{/if}

			{#if byCategory.length}
				<div class="space-y-1">
					{#each byCategory as [category, list] (category)}
						{@const passed = list.filter((c) => c.passed).length}
						<div>
							<button
								type="button"
								class="flex w-full cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-left text-xs transition-colors hover:bg-muted/50"
								aria-expanded={openCategory === category}
								onclick={() => (openCategory = openCategory === category ? null : category)}
							>
								<ChevronRight class="h-3.5 w-3.5 shrink-0 transition-transform duration-150 {openCategory === category ? 'rotate-90' : ''}" />
								<span class="font-medium capitalize">{category}</span>
								<span class="ml-auto font-mono tabular-nums {passed < list.length ? 'text-danger-fg' : 'text-muted-foreground'}">{passed}/{list.length}</span>
							</button>
							{#if openCategory === category}
								<ul class="mt-1 space-y-0.5 pl-6 text-xs">
									{#each list as c (c.name)}
										<li class="flex items-start gap-1.5">
											{#if c.passed}
												<CircleCheck class="mt-0.5 size-3.5 shrink-0 text-success-fg" aria-label="passed" />
											{:else}
												<CircleX class="mt-0.5 size-3.5 shrink-0 text-danger-fg" aria-label="failed" />
											{/if}
											<span class="min-w-0">
												<span class="font-mono">{c.name}</span>
												{#if c.detail}<span class="block break-words text-[11px] text-muted-foreground">{c.detail}</span>{/if}
											</span>
										</li>
									{/each}
								</ul>
							{/if}
						</div>
					{/each}
				</div>
			{/if}

			{#if evaluation.contract_stated_in_prompt === false}
				<p class="text-[11px] text-warning-fg">This sample's prompt did not state the conformance contract: compare its score with care.</p>
			{/if}
			{#if smokeOnly && functional}
				<p class="text-[11px] text-muted-foreground">
					metrics.functional of this run is the generation-time smoke check ({functional.endpoints_ok ?? '—'}/{functional.endpoints_checked ?? '—'} endpoints), not this suite.
				</p>
			{/if}

			{#if build}
				<div class="space-y-1.5">
					<SectionHeading size="sm" level={3} title="Build & runtime" />
					<dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-4">
						<div>
							<dt class="text-[11px] text-muted-foreground">Image build</dt>
							<dd><StatusBadge status={build.ok ? 'passed' : 'failed'} label={build.ok ? 'built' : 'failed'} /></dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground">Build attempts</dt>
							<dd class="font-mono tabular-nums">{attempts.length ? `${attempts.length} · ${attempts.map((a) => fmtSeconds(a.seconds)).join(' + ')}` : '—'}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground" title="Dominated by the stack's base image: context, not a quality measure">Image size</dt>
							<dd class="font-mono tabular-nums">{fmtBytes(build.image_bytes)}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground">Evaluation time</dt>
							<dd class="font-mono tabular-nums">{fmtSeconds(evaluation.seconds)}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground">App exit code</dt>
							<dd class="font-mono tabular-nums" title={evaluation.app_exit_code == null && build?.ok ? 'No exit code: the app was still running when the tests ended' : undefined}>{evaluation.app_exit_code ?? (build?.ok ? 'still running' : '—')}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground">OOM killed</dt>
							<dd class="font-mono">{evaluation.app_oom_killed == null ? '—' : evaluation.app_oom_killed ? 'yes' : 'no'}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground">Infra suspected</dt>
							<dd class="font-mono">{build.infra_suspected == null ? '—' : build.infra_suspected ? 'yes' : 'no'}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground" title="Python packages installed in the image (pip freeze)">Resolved Python deps</dt>
							<dd class="font-mono tabular-nums">{resolved.length || '—'}</dd>
						</div>
					</dl>
				</div>
			{/if}

			{#if healing}
				<div class="space-y-1">
					<SectionHeading size="sm" level={3} title="Build healing" />
					<p class="text-xs">
						<span class="text-muted-foreground">Mode</span> <span class="font-mono">{healing.mode ?? '—'}</span>
						· {#if healing.changed}<span class="text-warning-fg">the platform patched this app before building</span>{:else}<span class="text-muted-foreground">nothing patched</span>{/if}
					</p>
					{#if Array.isArray(healing.patches) && healing.patches.length}
						<div class="flex flex-wrap gap-1">
							{#each healing.patches as patch (patch)}<Badge variant="outline" class="font-mono text-[11px]">{patch}</Badge>{/each}
						</div>
					{/if}
					{#if Array.isArray(healing.added_requirements) && healing.added_requirements.length}
						<p class="text-xs"><span class="text-muted-foreground">Added requirements:</span> <span class="font-mono">{healing.added_requirements.join(', ')}</span></p>
					{/if}
					{#if healing.changed}
						<p class="text-[11px] text-muted-foreground">A pass that needed healing depended on platform repairs; keep that in mind when comparing scores.</p>
					{/if}
				</div>
			{/if}

			<div class="space-y-1">
				{#if evaluation.app_log_tail}
					<button type="button" class="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground" aria-expanded={showAppLog} onclick={() => (showAppLog = !showAppLog)}>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showAppLog ? 'rotate-90' : ''}" />App log (tail)
					</button>
					{#if showAppLog}
						<pre class="max-h-60 overflow-auto rounded border bg-muted/20 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">{evaluation.app_log_tail}</pre>
					{/if}
				{/if}
				{#if build?.log_tail}
					<button type="button" class="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground" aria-expanded={showBuildLog || !build.ok} onclick={() => (showBuildLog = !showBuildLog)}>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showBuildLog || !build.ok ? 'rotate-90' : ''}" />Build log (tail)
					</button>
					{#if showBuildLog || !build.ok}
						<pre class="max-h-60 overflow-auto rounded border bg-muted/20 p-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap">{build.log_tail}</pre>
					{/if}
				{/if}
			</div>

			<p class="font-mono text-[11px] text-muted-foreground">
				suite v{evaluation.tester_version ?? '—'} · contract v{evaluation.contract_version ?? '—'} · evaluator v{evaluation.evaluator_version ?? '—'}
				· tester <span title={evaluation.tester_sha256 ?? ''}>{shortHash(evaluation.tester_sha256)}</span>
				{#if evaluation.tester_image} · <span title={evaluation.tester_image_id ?? ''}>{evaluation.tester_image}</span>{/if}
			</p>

			{#if job.status === 'completed'}
				<div class="flex flex-wrap items-center gap-2">
					<Button variant="outline" size="sm" onclick={evaluate} disabled={evaluating}><RefreshCw class="h-3.5 w-3.5" />Re-evaluate</Button>
					{#if evaluateMessage}<span class="text-[11px] text-muted-foreground">{evaluateMessage}</span>{/if}
				</div>
			{/if}
		{/if}
		{#if !evaluation && evaluateMessage}
			<p class="text-[11px] text-muted-foreground">{evaluateMessage}</p>
		{/if}
	</Card.Content>
</Card.Root>
