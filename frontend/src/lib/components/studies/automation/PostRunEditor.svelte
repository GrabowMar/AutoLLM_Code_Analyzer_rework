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

	// Collapsed by default: a summary of the chosen analyzers; "Choose" opens them grouped by category.
	let open = $state(false);
	const usableTools = $derived((tools ?? []).filter(usable));
	const otherTools = $derived((tools ?? []).filter((t) => !usable(t)));
	const byCategory = $derived.by(() => {
		const groups = new Map<string, AnalyzerTool[]>();
		for (const t of usableTools) groups.set(t.category || 'other', [...(groups.get(t.category || 'other') ?? []), t]);
		return [...groups].sort((a, b) => a[0].localeCompare(b[0]));
	});
	const chosenNames = $derived((tools ?? []).filter((t) => value.tool_slugs.includes(t.slug)).map((t) => t.name));

	function setGroup(list: AnalyzerTool[], on: boolean) {
		const set = new Set(value.tool_slugs);
		for (const t of list) on ? set.add(t.slug) : set.delete(t.slug);
		update({ ...value, tool_slugs: [...set] });
	}

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
			<div class="pr-summary">
				<span class="min-w-0 truncate text-sm">
					{#if chosenNames.length}
						<b>{chosenNames.length} analyzer{chosenNames.length === 1 ? '' : 's'}</b>
						<span class="text-muted-foreground"> · {chosenNames.slice(0, 5).join(', ')}{chosenNames.length > 5 ? ` +${chosenNames.length - 5}` : ''}</span>
					{:else}
						<span class="text-muted-foreground">No post-run analysis</span>
					{/if}
				</span>
				<button type="button" class="pr-toggle" aria-expanded={open} onclick={() => (open = !open)} {disabled}>
					{open ? 'Done' : 'Choose'}
				</button>
			</div>
			{#if open}
				<div class="pr-groups">
					{#each byCategory as [category, list] (category)}
						{@const allOn = list.every((t) => value.tool_slugs.includes(t.slug))}
						<fieldset class="pr-group" aria-label="{category} analyzers">
							<legend class="pr-cat">
								{category}
								<button type="button" class="pr-all" onclick={() => setGroup(list, !allOn)} {disabled}>{allOn ? 'none' : 'all'}</button>
							</legend>
							<div class="pr-chips">
								{#each list as tool (tool.slug)}
									{@const on = value.tool_slugs.includes(tool.slug)}
									<button
										type="button"
										class="pr-chip"
										data-on={on}
										aria-pressed={on}
										title={tool.description}
										{disabled}
										onclick={() => toggleTool(tool.slug, !on)}>{tool.name}</button
									>
								{/each}
							</div>
						</fieldset>
					{/each}
					{#if otherTools.length}
						<p class="text-[11px] text-muted-foreground">
							Not available as a post-run: {#each otherTools as t, i (t.slug)}<span title={unusableReason(t)}>{t.name}{i < otherTools.length - 1 ? ', ' : ''}</span>{/each}
							(dynamic scanners run through the evaluation level "full").
						</p>
					{/if}
				</div>
			{/if}
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

<style>
	.pr-summary {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius);
		padding: 0.5rem 0.75rem;
	}
	.pr-toggle {
		height: 1.75rem;
		flex-shrink: 0;
		cursor: pointer;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--border);
		padding-inline: 0.625rem;
		font-size: 0.75rem;
		font-weight: 500;
	}
	.pr-toggle[aria-expanded='true'] {
		background: var(--muted);
	}
	.pr-groups {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	.pr-group {
		display: grid;
		grid-template-columns: 7.5rem minmax(0, 1fr);
		align-items: start;
		gap: 0.75rem;
	}
	.pr-cat {
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
		padding-top: 0.3rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--muted-foreground);
	}
	.pr-all {
		align-self: flex-start;
		cursor: pointer;
		font-family: var(--font-sans);
		font-size: 0.6875rem;
		text-transform: none;
		letter-spacing: normal;
		color: var(--primary);
	}
	.pr-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.375rem;
	}
	.pr-chip {
		height: 1.75rem;
		cursor: pointer;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--border);
		padding-inline: 0.625rem;
		font-size: 0.75rem;
		transition: border-color 150ms, background-color 150ms;
	}
	.pr-chip[data-on='true'] {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 12%, var(--card));
		color: var(--accent-foreground);
		font-weight: 500;
	}
	.pr-chip:focus-visible,
	.pr-toggle:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	@media (max-width: 640px) {
		.pr-group {
			grid-template-columns: minmax(0, 1fr);
			gap: 0.375rem;
		}
	}
</style>
