<script lang="ts">
	/**
	 * EvalSuiteViewer — read-only `EvalSuiteContent`: the pinned tester and adherence-checker images, the
	 * acceptance compiler and rule versions, probe selection, baseline, timeouts and the flaky checks that
	 * `oracle_validate` excluded. Suites are versioned by the platform (seed data and oracle validation),
	 * so the UI does not edit them; which outcomes a study measures is its evaluation level.
	 *
	 * Props: `value`.
	 */
	import type { EvalSuiteContent } from '$lib/api/v3/types';

	interface Props {
		value: EvalSuiteContent;
	}

	let { value }: Props = $props();

	const rows = $derived<[string, string][]>([
		['Tester', `v${value.tester.version}`],
		['Tester image', value.tester.image_digest],
		['Adherence checker', `v${value.adherence_checker.version}`],
		['Checker image', value.adherence_checker.image_digest],
		['Acceptance compiler', `v${value.acceptance_compiler_version}`],
		['Tester rules', `v${value.tester_rules_version}`],
		['Probe rules', `v${value.probe_rules_version}`],
		['Probes', value.probes.replace(/_/g, ' ')],
		['Baseline', value.baseline === 'starter' ? 'starter (measured and shown)' : 'none'],
		['Timeouts', `build ${value.timeouts.build_s} s · start ${value.timeouts.start_s} s · tester ${value.timeouts.tester_s} s`],
	]);
</script>

<div class="space-y-4">
	<dl class="divide-y divide-border rounded-md border border-border">
		{#each rows as [label, text] (label)}
			<div class="grid gap-1 px-3 py-2 sm:grid-cols-[12rem_minmax(0,1fr)]">
				<dt class="text-sm text-muted-foreground">{label}</dt>
				<dd class="break-all font-mono text-xs">{text}</dd>
			</div>
		{/each}
	</dl>
	<div class="space-y-1">
		<p class="section-label">Flaky checks <span class="font-mono">{value.flaky_checks.length}</span></p>
		{#if value.flaky_checks.length}
			<ul class="flex flex-wrap gap-1.5">
				{#each value.flaky_checks as id (id)}<li class="rounded-sm border border-border px-1.5 font-mono text-[11px]">{id}</li>{/each}
			</ul>
			<p class="text-xs text-muted-foreground">Excluded from pass rates (they failed oracle test-retest).</p>
		{:else}
			<p class="text-xs text-muted-foreground">None: every compiled check passed the oracle test-retest.</p>
		{/if}
	</div>
</div>
