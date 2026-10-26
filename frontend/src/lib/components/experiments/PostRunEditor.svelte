<script lang="ts">
	/**
	 * PostRunEditor — what happens after the runs: evaluate (scaffolding;
	 * agent runs are graded inline), static analysis with chosen analyzers
	 * (optionally only on passing runs), then the experiment report.
	 *
	 * Props:
	 * - `value` (bindable `PostRunSpec`): replaced, never mutated.
	 * - `mode`: 'agent' | 'scaffolding'. `idPrefix`, `disabled`, `onchange()`.
	 */
	import { onMount } from 'svelte';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Switch } from '$lib/components/ui/switch';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { getToolCatalog, type AnalyzerTool } from '$lib/api/analyzers';
	import { formatApiError } from '$lib/api/core';
	import type { PostRunSpec } from '$lib/api/types/experiments';

	interface Props {
		value: PostRunSpec;
		mode: 'agent' | 'scaffolding';
		idPrefix?: string;
		disabled?: boolean;
		onchange?: (value: PostRunSpec) => void;
	}

	let { value = $bindable(), mode, idPrefix = 'post-run', disabled = false, onchange }: Props = $props();

	let tools = $state<AnalyzerTool[] | null>(null);
	let toolsError = $state('');

	async function loadTools() {
		toolsError = '';
		try {
			tools = (await getToolCatalog()).filter((t) => t.is_enabled);
		} catch (e) {
			toolsError = formatApiError(e, 'Could not load the analyzers.');
		}
	}

	onMount(() => {
		if (value.analysis.enabled) void loadTools();
	});

	function update(next: PostRunSpec) {
		value = next;
		onchange?.(next);
	}

	function setAnalysis(patch: Partial<PostRunSpec['analysis']>) {
		update({ ...value, analysis: { ...value.analysis, ...patch } });
		if (patch.enabled && !tools) void loadTools();
	}

	function toggleTool(slug: string, on: boolean) {
		const set = new Set(value.analysis.tool_slugs);
		if (on) set.add(slug);
		else set.delete(slug);
		setAnalysis({ tool_slugs: [...set] });
	}

	// Dynamic scanners need the app running, and evaluation tears apps down.
	const isDynamic = (t: AnalyzerTool) => t.analysis_mode === 'dynamic';
	const usable = (t: AnalyzerTool) => !isDynamic(t) && (t.kind === 'ai' || t.installed);

	function unusableReason(t: AnalyzerTool): string {
		if (isDynamic(t)) return "Dynamic scanner: needs the app running, which post-run analysis doesn't do yet";
		return 'Not installed in your analyzer workspace';
	}
</script>

<div class="space-y-4">
	{#if mode === 'scaffolding'}
		<div class="flex items-start gap-3">
			<Switch
				id="{idPrefix}-evaluate"
				checked={value.evaluate}
				{disabled}
				describedBy="{idPrefix}-evaluate-note"
				onCheckedChange={(v) => update({ ...value, evaluate: v })}
			/>
			<div class="min-w-0 space-y-0.5">
				<label for="{idPrefix}-evaluate" class="text-sm font-medium">Evaluate every run</label>
				<p id="{idPrefix}-evaluate-note" class="text-xs text-muted-foreground">
					Builds the app and runs its hidden conformance suite, so Results can rank conditions. Without it runs are
					ungraded.
				</p>
			</div>
		</div>
	{/if}

	<div class="space-y-2">
		<div class="flex items-start gap-3">
			<Switch
				id="{idPrefix}-analysis"
				checked={value.analysis.enabled}
				{disabled}
				describedBy="{idPrefix}-analysis-note"
				onCheckedChange={(v) => setAnalysis({ enabled: v })}
			/>
			<div class="min-w-0 space-y-0.5">
				<label for="{idPrefix}-analysis" class="text-sm font-medium">Static analysis when the experiment completes</label>
				<p id="{idPrefix}-analysis-note" class="text-xs text-muted-foreground">
					Runs the chosen analyzers over the finished runs, one at a time, after every cell is done.
				</p>
			</div>
		</div>
		{#if value.analysis.enabled}
			<div class="ml-12 space-y-2">
				{#if toolsError}
					<ErrorAlert message={toolsError} onretry={loadTools} />
				{:else if !tools}
					<p class="loading-mono">loading analyzers</p>
				{:else if tools.length === 0}
					<p class="text-xs text-muted-foreground">No analyzers are enabled. <a href="/analyzers" class="underline">Set them up</a>.</p>
				{:else}
					<fieldset class="grid gap-1.5 sm:grid-cols-2" aria-label="Analyzers">
						{#each tools as tool (tool.slug)}
							<label class="flex items-center gap-2 text-xs" title={usable(tool) ? tool.description : unusableReason(tool)}>
								<Checkbox
									checked={value.analysis.tool_slugs.includes(tool.slug)}
									disabled={disabled || !usable(tool)}
									onCheckedChange={(v) => toggleTool(tool.slug, v === true)}
								/>
								<span class="truncate">{tool.name}</span>
								<span class="text-xs text-muted-foreground"
									>{tool.category}{isDynamic(tool) ? ' · needs running app' : usable(tool) ? '' : ' · not installed'}</span
								>
							</label>
						{/each}
					</fieldset>
					{#if value.analysis.tool_slugs.length === 0}
						<p class="text-xs text-warning-fg">Pick at least one analyzer.</p>
					{/if}
				{/if}
				<label class="flex items-center gap-2 text-xs">
					<Checkbox
						checked={value.analysis.only_passing}
						{disabled}
						onCheckedChange={(v) => setAnalysis({ only_passing: v === true })}
					/>
					Only runs that passed every check
				</label>
			</div>
		{/if}
	</div>

	<div class="space-y-2">
		<div class="flex items-start gap-3">
			<Switch
				id="{idPrefix}-report"
				checked={value.report.enabled}
				{disabled}
				describedBy="{idPrefix}-report-note"
				onCheckedChange={(v) => update({ ...value, report: { ...value.report, enabled: v } })}
			/>
			<div class="min-w-0 space-y-0.5">
				<label for="{idPrefix}-report" class="text-sm font-medium">Experiment report</label>
				<p id="{idPrefix}-report-note" class="text-xs text-muted-foreground">
					Generated after analysis: leaderboard and paired comparisons against the baseline.
				</p>
			</div>
		</div>
		{#if value.report.enabled}
			<label class="ml-12 flex items-center gap-2 text-xs">
				<Checkbox
					checked={value.report.exclude_truncated}
					{disabled}
					onCheckedChange={(v) => update({ ...value, report: { ...value.report, exclude_truncated: v === true } })}
				/>
				Exclude truncated runs (budget or wall clock hit)
			</label>
		{/if}
	</div>
	{#if mode === 'agent'}
		<p class="text-xs text-muted-foreground">Agent runs are evaluated as they finish; no separate step is needed.</p>
	{/if}
</div>
