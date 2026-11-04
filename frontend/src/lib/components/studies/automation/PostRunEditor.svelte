<script lang="ts" module>
	/** The analysis post-run config (`study/automation.py:normalize_post_run_config`, kind `analysis`). */
	export interface AnalysisPostRunConfig {
		tool_slugs: string[];
		only_passing: boolean;
		report: boolean;
	}

	export function defaultAnalysisConfig(): AnalysisPostRunConfig {
		return { tool_slugs: [], only_passing: false, report: true };
	}
</script>

<script lang="ts">
	/**
	 * PostRunEditor — what runs after the study's runs: static analysis with chosen analyzers (optionally
	 * only on passing runs, which biases conditional analyses and is shown as such) and the study report.
	 * Slot studies get a patterns post-run attached automatically (no LLM cost), shown read-only.
	 *
	 * Props:
	 * - `value` (bindable `AnalysisPostRunConfig`): replaced, never mutated.
	 * - `mode`: the study mode (`slot` shows the auto-attached patterns post-run).
	 * - `idPrefix`, `disabled`, `onchange(value)`.
	 */
	import { onMount } from 'svelte';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Switch } from '$lib/components/ui/switch';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { getToolCatalog, type AnalyzerTool } from '$lib/api/analyzers';
	import { formatApiError } from '$lib/api/core';
	import type { StudyMode } from '$lib/api/v3/types';

	interface Props {
		value: AnalysisPostRunConfig;
		mode: StudyMode;
		idPrefix?: string;
		disabled?: boolean;
		onchange?: (value: AnalysisPostRunConfig) => void;
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
		void loadTools();
	});

	function update(next: AnalysisPostRunConfig) {
		value = next;
		onchange?.(next);
	}

	function toggleTool(slug: string, on: boolean) {
		const set = new Set(value.tool_slugs);
		if (on) set.add(slug);
		else set.delete(slug);
		update({ ...value, tool_slugs: [...set] });
	}

	// Dynamic scanners need the app running; post-run analysis reads the app-scope tree only.
	const isDynamic = (t: AnalyzerTool) => t.analysis_mode === 'dynamic';
	const usable = (t: AnalyzerTool) => !isDynamic(t) && (t.kind === 'ai' || t.installed);

	function unusableReason(t: AnalyzerTool): string {
		if (isDynamic(t)) return 'Dynamic scanner: runs through the evaluation level "full", not as a post-run';
		return 'Not installed in your analyzer workspace';
	}
</script>

<div class="space-y-4">
	<div class="space-y-2">
		<p class="text-sm font-medium">Static analysis when the experiment completes</p>
		<p class="text-xs text-muted-foreground">
			Runs the chosen analyzers over the usable runs that a tool has not measured yet. A tool with no applicable files records
			"not measured", never a clean result.
		</p>
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
							checked={value.tool_slugs.includes(tool.slug)}
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
		{/if}
		<label class="flex items-center gap-2 text-xs">
			<Checkbox checked={value.only_passing} {disabled} onCheckedChange={(v) => update({ ...value, only_passing: v === true })} />
			Only runs that passed every check
		</label>
		{#if value.only_passing}
			<p class="text-xs text-warning-fg">Analysing only passing runs biases conditional analyses; it is recorded with the results.</p>
		{/if}
	</div>

	<div class="flex items-start gap-3">
		<Switch
			id="{idPrefix}-report"
			checked={value.report}
			{disabled}
			describedBy="{idPrefix}-report-note"
			onCheckedChange={(v) => update({ ...value, report: v })}
		/>
		<div class="min-w-0 space-y-0.5">
			<label for="{idPrefix}-report" class="text-sm font-medium">Experiment report</label>
			<p id="{idPrefix}-report-note" class="text-xs text-muted-foreground">
				Generated after analysis: leaderboard and paired comparisons against the baseline, intention-to-treat.
			</p>
		</div>
	</div>

	{#if mode === 'slot'}
		<p class="rounded-md border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
			Slot experiments also run the pattern analysis (default detectors) automatically when they complete. It needs no LLM calls; a
			confirmatory experiment's pre-registered pattern plan is used as is.
		</p>
	{/if}
</div>
