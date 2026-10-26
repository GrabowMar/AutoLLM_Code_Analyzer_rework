<script lang="ts" module>
	/** A likely cause and next step for common failure messages (first match wins). */
	const HINTS: { test: RegExp; hint: string }[] = [
		{
			test: /\b429\b|rate.?limit/i,
			hint: 'The provider is rate-limiting this model. Free (“:free”) models share one upstream pool: retry later, or clone the experiment with a paid variant.',
		},
		{ test: /\b402\b|insufficient (credit|funds)|out of credit/i, hint: 'The OpenRouter account is out of credit.' },
		{ test: /\b40[13]\b|api key|unauthori[sz]ed|forbidden/i, hint: 'The provider rejected the API key: check Settings → API keys.' },
		{ test: /timed? ?out|timeout|wall.?clock/i, hint: 'The run hit a time limit. If it repeats, raise the wall clock in the budget preset.' },
	];

	export function failureHint(message: string): string | null {
		return HINTS.find((h) => h.test.test(message))?.hint ?? null;
	}
</script>

<script lang="ts">
	/**
	 * FailureSummary — why this experiment's runs failed: current failed
	 * attempts grouped by error message (most frequent first), each with a
	 * likely cause when the message is a known one and a link to one example run.
	 * Renders nothing while no run has failed.
	 *
	 * Props:
	 * - `experimentId`.
	 * - `failed`: failed + cancelled cell count from the live status; a change refetches.
	 */
	import { untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { getGenerationJobs, type GenerationJobList } from '$lib/api/generation';
	import { plural } from '../format';

	interface Props {
		experimentId: string;
		failed: number;
	}

	let { experimentId, failed }: Props = $props();

	const MAX_GROUPS = 5;

	let jobs = $state.raw<GenerationJobList[] | null>(null);
	let total = $state(0);
	let error = $state('');
	let seq = 0;

	async function load() {
		const mine = ++seq;
		try {
			const res = await getGenerationJobs({ experiment_id: experimentId, status: 'failed', current: true, per_page: 100, sort_by: 'sequence' });
			if (mine !== seq) return;
			jobs = res.items;
			total = res.total;
			error = '';
		} catch (e) {
			if (mine === seq) error = formatApiError(e, 'Could not load the failed runs.');
		}
	}

	$effect(() => {
		void experimentId;
		if (failed <= 0) return;
		untrack(() => void load());
	});

	const groups = $derived.by(() => {
		const byMessage = new Map<string, { message: string; count: number; exampleId: string }>();
		for (const job of jobs ?? []) {
			const message = (job.error_message || 'No error message recorded').replace(/\s+/g, ' ').trim();
			const group = byMessage.get(message);
			if (group) group.count += 1;
			else byMessage.set(message, { message, count: 1, exampleId: job.id });
		}
		return [...byMessage.values()].sort((a, b) => b.count - a.count);
	});
</script>

{#if failed > 0 && (error || groups.length > 0)}
	<Card.Root class="gap-0 py-0">
		<Card.Header class="flex-row items-center justify-between gap-2 border-b border-border py-3">
			<div class="space-y-1">
				<Card.Title>Why runs failed</Card.Title>
				<Card.Description>
					{plural(total, 'failed run')}{groups.length > 1 ? `, ${plural(groups.length, 'distinct error')}` : ''}. Failed cells can be re-run with their original seeds.
				</Card.Description>
			</div>
			<Button variant="ghost" size="sm" href="?tab=runs">Open runs</Button>
		</Card.Header>
		<Card.Content class="p-0">
			{#if error}
				<div class="p-3"><ErrorAlert message={error} onretry={load} /></div>
			{:else}
				<ul class="divide-y divide-border/60">
					{#each groups.slice(0, MAX_GROUPS) as group (group.message)}
						{@const hint = failureHint(group.message)}
						<li class="flex min-w-0 items-start gap-3 px-4 py-2.5 text-sm">
							<span class="mt-px w-10 shrink-0 text-right font-mono tabular-nums text-danger-fg" title={plural(group.count, 'run')}>{group.count}×</span>
							<div class="min-w-0 flex-1 space-y-0.5">
								<p class="line-clamp-2 break-words font-mono text-xs text-foreground" title={group.message}>{group.message}</p>
								{#if hint}<p class="text-xs text-muted-foreground">{hint}</p>{/if}
							</div>
							<a class="shrink-0 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline" href="/applications/{group.exampleId}">
								Example run
							</a>
						</li>
					{/each}
				</ul>
				{#if groups.length > MAX_GROUPS}
					<p class="border-t border-border/60 px-4 py-2 text-xs text-muted-foreground">
						{plural(groups.length - MAX_GROUPS, 'more error')} — see Runs.
					</p>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
{/if}
