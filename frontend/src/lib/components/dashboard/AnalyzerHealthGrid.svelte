<script lang="ts">
	/**
	 * Analyzer health from the canary self-tests on the current analyzer image:
	 * a tool is "passed" only when it is enabled and passed its latest
	 * self-test; "untested" tools have no evidence that they work, "failed"
	 * ones missed expected findings or did not run.
	 *
	 * Props: `health` (null while loading), `isRefreshing`, `onRefresh`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import HeartPulse from '@lucide/svelte/icons/heart-pulse';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import type {
		AnalyzerHealth,
		AnalyzerHealthRow,
		AnalyzerHealthStatus
	} from '$lib/api/statistics';

	interface Props {
		health: AnalyzerHealth | null;
		isRefreshing: boolean;
		onRefresh: () => void;
	}

	let { health, isRefreshing, onRefresh }: Props = $props();

	const ORDER: AnalyzerHealthStatus[] = ['failed', 'untested', 'passed', 'disabled'];

	function statusOf(a: AnalyzerHealthRow): AnalyzerHealthStatus {
		if (a.status) return a.status;
		// Older payloads: `available` was the only signal; anything else is unverified.
		return a.available ? 'passed' : 'untested';
	}

	const counts = $derived.by(() => {
		const out: Record<AnalyzerHealthStatus, number> = {
			passed: 0,
			failed: 0,
			untested: 0,
			disabled: 0
		};
		for (const a of health?.analyzers ?? []) out[statusOf(a)] += 1;
		return out;
	});

	const sorted = $derived(
		[...(health?.analyzers ?? [])].sort(
			(a, b) =>
				ORDER.indexOf(statusOf(a)) - ORDER.indexOf(statusOf(b)) ||
				a.display_name.localeCompare(b.display_name)
		)
	);

	const selftest = $derived(health?.selftest ?? null);

	function when(iso: string | null): string {
		if (!iso) return '';
		const d = new Date(iso);
		return Number.isNaN(d.getTime())
			? iso
			: d.toLocaleString(undefined, {
					month: 'short',
					day: 'numeric',
					hour: '2-digit',
					minute: '2-digit'
				});
	}
</script>

<Card.Root>
	<Card.Header>
		<div class="flex items-start justify-between gap-2">
			<div class="flex min-w-0 items-start gap-2">
				<HeartPulse class="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
				<div class="min-w-0">
					<Card.Title>Analyzer health</Card.Title>
					<Card.Description>
						{#if health}
							Self-test verdicts on the current analyzer image ·
							<span class="font-medium text-success-fg">{counts.passed} passed</span>
							{#if counts.failed}· <span class="font-medium text-danger-fg"
									>{counts.failed} failed</span
								>{/if}
							{#if counts.untested}· <span class="font-medium text-warning-fg"
									>{counts.untested} untested</span
								>{/if}
							{#if counts.disabled}· {counts.disabled} disabled{/if}
						{:else}
							Loading self-test verdicts…
						{/if}
					</Card.Description>
				</div>
			</div>
			<Button
				variant="ghost"
				size="icon"
				class="h-7 w-7 shrink-0"
				onclick={onRefresh}
				disabled={isRefreshing}
				aria-label="Refresh analyzer health"
			>
				<RefreshCw
					class="h-3.5 w-3.5 {isRefreshing ? 'animate-spin motion-reduce:animate-none' : ''}"
				/>
			</Button>
		</div>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if health}
			<div
				class="flex flex-wrap items-center gap-x-3 gap-y-1.5 rounded-md border border-border bg-muted/30 px-3 py-2 text-xs"
			>
				<span class="section-label">Latest self-test</span>
				{#if selftest}
					<StatusBadge
						status={selftest.passed === true
							? 'passed'
							: selftest.passed === false
								? 'failed'
								: 'untested'}
						label={selftest.passed === true
							? 'all checks passed'
							: selftest.passed === false
								? 'some checks failed'
								: 'not evaluated'}
					/>
					{#if selftest.image_tag}<span class="mono-tag" title="Analyzer image"
							>{selftest.image_tag}</span
						>{/if}
					{#if selftest.evaluated_at}<span class="text-muted-foreground"
							>{when(selftest.evaluated_at)}</span
						>{/if}
					{#if selftest.run_id}
						<a
							href="/analysis/{selftest.run_id}"
							class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
						>
							View run <ArrowRight class="h-3 w-3" aria-hidden="true" />
						</a>
					{/if}
				{:else}
					<span class="text-warning-fg">No self-test on this image — no tool is verified yet.</span>
					<a
						href="/analysis/tools"
						class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
					>
						Run a self-test <ArrowRight class="h-3 w-3" aria-hidden="true" />
					</a>
				{/if}
			</div>

			{#if sorted.length === 0}
				<p class="text-sm text-muted-foreground">No analyzer tools in the catalog.</p>
			{:else}
				<ul class="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
					{#each sorted as a (a.name)}
						{@const status = statusOf(a)}
						<li
							class="flex min-w-0 flex-col gap-1.5 rounded-md border border-border bg-surface-2/50 px-3 py-2.5"
						>
							<div class="flex items-center justify-between gap-2">
								<span class="truncate text-sm font-medium" title={a.name}>{a.display_name}</span>
								<StatusBadge {status} />
							</div>
							<div class="flex items-center justify-between gap-2">
								<span class="mono-tag uppercase">{a.type}</span>
								{#if a.version}
									<span
										class="truncate font-mono text-[11px] text-muted-foreground"
										title="Version from the image manifest">v{a.version}</span
									>
								{/if}
							</div>
							{#if status !== 'passed'}
								<p class="line-clamp-2 text-[11px] text-muted-foreground" title={a.message}>
									{a.message}
								</p>
							{/if}
						</li>
					{/each}
				</ul>
			{/if}
		{:else}
			<div
				class="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
				aria-hidden="true"
			>
				{#each Array(4) as _, i (i)}
					<div class="skeleton h-[4.25rem]"></div>
				{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
