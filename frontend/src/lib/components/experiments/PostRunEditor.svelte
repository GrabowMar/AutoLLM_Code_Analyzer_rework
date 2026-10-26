<script lang="ts">
	/**
	 * PostRunEditor — what happens after the runs: evaluate (scaffolding;
	 * agent runs are graded inline), analysis under one frozen v2 plan (an
	 * analysis profile, optionally restricted to some of its tools; optionally
	 * only on passing runs), then the experiment report.
	 *
	 * Props:
	 * - `value` (bindable `PostRunSpec`): replaced, never mutated.
	 * - `mode`: 'agent' | 'scaffolding'. `idPrefix`, `disabled`, `onchange()`.
	 */
	import { onMount } from 'svelte';
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Switch } from '$lib/components/ui/switch';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { listAnalysisProfiles, listAnalysisTools } from '$lib/api/analysis';
	import type { AnalysisProfile, AnalysisTool } from '$lib/api/analysis';
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

	let tools = $state<AnalysisTool[] | null>(null);
	let profiles = $state<AnalysisProfile[]>([]);
	let toolsError = $state('');

	async function loadTools() {
		toolsError = '';
		try {
			const [catalog, list] = await Promise.all([listAnalysisTools(), listAnalysisProfiles()]);
			tools = catalog.filter((t) => t.is_enabled);
			profiles = list.filter((p) => !p.is_archived);
		} catch (e) {
			toolsError = formatApiError(e, 'Could not load the analysis tools and profiles.');
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

	const profileItems = $derived(
		profiles.map((p) => ({
			value: p.slug,
			label: p.name,
			description: `${p.slug} v${p.version}${p.is_system ? ' · system' : ''}`
		}))
	);
	const profileRef = $derived(value.analysis.profile ?? '');
	const needsChoice = $derived(!profileRef && value.analysis.tool_slugs.length === 0);
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
				<p id="{idPrefix}-evaluate-note" class="text-[11px] text-muted-foreground">
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
				<p id="{idPrefix}-analysis-note" class="text-[11px] text-muted-foreground">
					Analyses the finished runs under one frozen plan after every cell is done.
				</p>
			</div>
		</div>
		{#if value.analysis.enabled}
			<div class="ml-12 space-y-2">
				{#if toolsError}
					<ErrorAlert message={toolsError} onretry={loadTools} />
				{:else if !tools}
					<p class="loading-mono">loading analysis tools</p>
				{:else}
					<div class="max-w-sm space-y-1">
						<label for="{idPrefix}-profile" class="text-xs font-medium">Analysis profile</label>
						<Select.Simple
							id="{idPrefix}-profile"
							size="sm"
							value={profileRef}
							items={profileItems}
							placeholder="Pick a profile"
							clearLabel="No profile (tools below restrict the default)"
							{disabled}
							onValueChange={(v) => setAnalysis({ profile: v || null })}
						/>
						<p class="text-[11px] text-muted-foreground">
							Frozen into one plan when the post-run starts, so every run of it is comparable.
							<a href="/analysis/profiles" class="underline">Manage profiles</a>
						</p>
					</div>
					{#if tools.length > 0}
						<fieldset class="space-y-1.5">
							<legend class="text-xs font-medium">Only these tools (optional)</legend>
							<div class="grid gap-1.5 sm:grid-cols-2">
								{#each tools as tool (tool.slug)}
									<label class="flex items-center gap-2 text-xs" title={tool.description}>
										<Checkbox
											checked={value.analysis.tool_slugs.includes(tool.slug)}
											{disabled}
											onCheckedChange={(v) => toggleTool(tool.slug, v === true)}
										/>
										<span class="truncate">{tool.name}</span>
										<span class="text-[11px] text-muted-foreground"
											>{tool.category}{tool.default_enabled ? '' : ' · opt-in'}</span
										>
									</label>
								{/each}
							</div>
							<p class="text-[11px] text-muted-foreground">
								With a profile, leave empty to run its own tools. <a href="/analysis/tools" class="underline"
									>About the tools</a
								>
							</p>
						</fieldset>
					{/if}
					{#if needsChoice}
						<p class="text-[11px] text-warning-fg">
							Pick a profile, or tools to run under the default research profile.
						</p>
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
				<p id="{idPrefix}-report-note" class="text-[11px] text-muted-foreground">
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
		<p class="text-[11px] text-muted-foreground">Agent runs are evaluated as they finish; no separate step is needed.</p>
	{/if}
</div>
