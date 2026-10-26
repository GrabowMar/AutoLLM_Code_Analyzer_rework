<script lang="ts">
	/**
	 * AnalyzeStepConfig — configuration of an automation `analyze` step from
	 * the real catalog: analysis profile (latest or a pinned version), an
	 * optional tool restriction, the target job, finding thresholds, timeout
	 * and failure policy. See `analyze-step.ts` for the config keys.
	 *
	 * Props: `config` (the step config; other keys such as `_position` are
	 * preserved), `onchange(next)`, `upstream` (names of generate steps whose
	 * output can feed the target), `idPrefix`.
	 */
	import { onMount } from 'svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		listAnalysisProfiles,
		listAnalysisTools,
		type AnalysisProfile,
		type AnalysisTool
	} from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import NumberField from '$lib/components/analysis/profile/NumberField.svelte';
	import { reviewerSummary } from '$lib/components/analysis/profile/profile-utils';
	import {
		AI_REVIEW_SLUG,
		ANALYZE_DEFAULT_TIMEOUT_SECONDS,
		ANALYZE_MAX_TIMEOUT_SECONDS,
		THRESHOLD_KEYS,
		hasLegacyKeys,
		isUuid,
		migrateAnalyzeConfig,
		profileRef,
		thresholdsOf,
		toolRestriction,
		type ThresholdKey
	} from './analyze-step';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		config: Record<string, unknown>;
		onchange?: (next: Record<string, unknown>) => void;
		upstream?: string[];
		idPrefix?: string;
	}

	let { config, onchange, upstream = [], idPrefix = 'analyze-step' }: Props = $props();

	let profiles = $state<AnalysisProfile[]>([]);
	let tools = $state<AnalysisTool[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	async function load() {
		loading = true;
		loadError = '';
		try {
			[profiles, tools] = await Promise.all([listAnalysisProfiles(), listAnalysisTools()]);
		} catch (e) {
			loadError = formatApiError(e, 'Could not load analysis profiles and tools.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	const id = (name: string) => `${idPrefix}-${name}`;

	/** Next config: `undefined` removes a key. */
	function update(patch: Record<string, unknown>) {
		const next: Record<string, unknown> = { ...config };
		for (const [key, value] of Object.entries(patch)) {
			if (value === undefined) delete next[key];
			else next[key] = value;
		}
		onchange?.(next);
	}

	const legacy = $derived(hasLegacyKeys(config));

	// ── profile ────────────────────────────────────────────────────────
	const ref = $derived(profileRef(config));
	const pinned = $derived(isUuid(ref));
	const selected = $derived(profiles.find((p) => (pinned ? p.id === ref : p.slug === ref)) ?? null);
	const profileItems = $derived(
		profiles.map((p) => ({
			value: p.slug,
			label: `${p.name} · v${p.version}`,
			description: `${p.summary.tool_count} tools · ${reviewerSummary(p.summary.reviewer)}${p.is_system ? ' · system' : ''}`
		}))
	);

	function setProfile(slug: string) {
		update({
			profile: slug || undefined,
			profile_id: undefined,
			profile_slug: undefined,
			profile_name: undefined
		});
	}

	function setPinned(on: boolean) {
		if (!selected) return;
		update({
			profile: on ? selected.id : selected.slug,
			profile_id: undefined,
			profile_slug: undefined
		});
	}

	// ── tools ──────────────────────────────────────────────────────────
	const restriction = $derived(toolRestriction(config));
	const restricted = $derived(restriction !== null);
	const toolMode = $derived(restricted || !ref ? 'only' : 'profile');
	const catalogSlugs = $derived(new Set(tools.map((t) => t.slug)));
	const unknownTools = $derived(
		tools.length > 0 ? (restriction ?? []).filter((slug) => !catalogSlugs.has(slug)) : []
	);
	const profileTools = $derived(new Set(selected?.summary.tools ?? []));
	const toolItems = $derived(
		[...tools]
			.sort((a, b) => a.display_order - b.display_order)
			.map((t) => ({ slug: t.slug, label: t.kind === 'ai' ? 'AI reviewer' : t.name }))
	);

	function setToolMode(mode: string) {
		if (mode === 'profile') {
			update({ tool_slugs: undefined, analyzers: undefined });
		} else if (!restricted) {
			// Start from the profile's tools (+ the AI reviewer when it is on).
			const start = selected
				? [
						...selected.summary.tools,
						...(selected.summary.reviewer.enabled ? [AI_REVIEW_SLUG] : [])
					]
				: [];
			update({ tool_slugs: start, analyzers: undefined });
		}
	}

	function toggleTool(slug: string, on: boolean) {
		const current = new Set(restriction ?? []);
		if (on) current.add(slug);
		else current.delete(slug);
		// Catalog order, unknown slugs kept until removed explicitly.
		const ordered = [
			...toolItems.map((t) => t.slug).filter((s) => current.has(s)),
			...[...current].filter((s) => !catalogSlugs.has(s))
		];
		update({ tool_slugs: ordered, analyzers: undefined });
	}

	const toolsProblem = $derived.by(() => {
		if (!ref && restriction === null) return 'Pick a profile or select tools.';
		// With a profile, an empty list is ignored by the backend (the profile runs unchanged).
		if (restriction !== null && restriction.length === 0 && !ref)
			return 'Select at least one tool.';
		if (unknownTools.length > 0)
			return `Unknown tool${unknownTools.length === 1 ? '' : 's'}: the step fails until removed.`;
		return '';
	});

	// ── target / gates / timeout ───────────────────────────────────────
	const jobRef = $derived(
		typeof config.generation_job_id === 'string' ? config.generation_job_id : ''
	);
	const thresholds = $derived(thresholdsOf(config));
	const thresholdCount = $derived(Object.keys(thresholds).length);

	function setThreshold(key: ThresholdKey, value: number | null) {
		const next: Record<string, number> = { ...thresholds };
		if (value === null) delete next[key];
		else next[key] = value;
		update({ thresholds: Object.keys(next).length ? next : undefined });
	}

	const timeout = $derived(
		typeof config.timeout_seconds === 'number' ? config.timeout_seconds : null
	);
	const thresholdLabels: Record<ThresholdKey, string> = {
		critical: 'Critical',
		high: 'High',
		medium: 'Medium',
		low: 'Low',
		info: 'Info',
		total: 'Total'
	};
</script>

<div class="space-y-4" data-slot="analyze-step-config">
	{#if legacy}
		<div
			class="space-y-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs"
			role="alert"
		>
			<p class="flex items-center gap-1.5 font-medium text-warning-fg">
				<TriangleAlert class="size-3.5 shrink-0" aria-hidden="true" />Settings from the old analyzer
			</p>
			<p class="text-muted-foreground">
				{#if config.live_target}Live-target scanning no longer exists and is ignored.{' '}{/if}Update
				the step to the current keys (tool list, profile, job reference).
			</p>
			<Button
				variant="outline"
				size="sm"
				class="h-7 px-2"
				onclick={() => onchange?.(migrateAnalyzeConfig(config))}
			>
				Update settings
			</Button>
		</div>
	{/if}

	{#if loadError}
		<ErrorAlert message={loadError} onretry={load} />
	{/if}

	<!-- Profile -->
	<div class="space-y-1.5">
		<Label for={id('profile')} class="text-xs">Analysis profile</Label>
		{#if loading}
			<p class="flex items-center gap-1.5 text-xs text-muted-foreground" role="status">
				<LoaderCircle
					class="size-3.5 animate-spin motion-reduce:animate-none"
					aria-hidden="true"
				/>loading profiles
			</p>
		{:else}
			<Select.Simple
				id={id('profile')}
				size="sm"
				value={selected?.slug ?? (pinned ? '' : ref)}
				items={profileItems}
				placeholder={pinned && !selected ? 'Pinned version' : 'No profile'}
				clearLabel="No profile (research default + tool list)"
				onValueChange={setProfile}
			/>
			{#if ref && !selected && !pinned}
				<p class="text-[11px] text-danger-fg">
					Profile “{ref}” is not available (archived or not yours); the step fails.
				</p>
			{:else if pinned && !selected}
				<p class="text-[11px] text-muted-foreground">
					Pinned to an older version
					<a
						href="/analysis/profiles/{ref}"
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-0.5 rounded font-mono text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
						>{ref.slice(0, 8)}<ExternalLink class="size-3" aria-hidden="true" /></a
					>
				</p>
			{/if}
			{#if selected}
				<p class="text-[11px] leading-4 text-muted-foreground">
					{selected.summary.tool_count} tools · {reviewerSummary(selected.summary.reviewer)} ·
					<a
						href="/analysis/profiles/{selected.id}"
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-0.5 rounded text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
						>open<ExternalLink class="size-3" aria-hidden="true" /><span class="sr-only">
							(new tab)</span
						></a
					>
				</p>
				<label class="flex cursor-pointer items-center gap-2 text-xs">
					<Checkbox checked={pinned} onCheckedChange={(on) => setPinned(on === true)} />
					Pin v{selected.version}
					<span class="text-muted-foreground">(otherwise the latest version at run time)</span>
				</label>
			{/if}
		{/if}
	</div>

	<!-- Tools -->
	<div class="space-y-2">
		<span id={id('tools-label')} class="text-xs font-medium">Tools</span>
		<Segmented
			aria-labelledby={id('tools-label')}
			size="sm"
			fullWidth
			value={toolMode}
			items={[
				{ value: 'profile', label: "Profile's tools", disabled: !ref },
				{ value: 'only', label: 'Only these' }
			]}
			onValueChange={setToolMode}
		/>
		{#if toolMode === 'only'}
			{#if tools.length > 0}
				<div
					class="grid grid-cols-2 gap-x-3 gap-y-1.5"
					role="group"
					aria-labelledby={id('tools-label')}
				>
					{#each toolItems as tool (tool.slug)}
						<label
							class="flex cursor-pointer items-center gap-1.5 text-xs"
							title={profileTools.has(tool.slug) ? 'In the profile' : undefined}
						>
							<Checkbox
								checked={(restriction ?? []).includes(tool.slug)}
								onCheckedChange={(on) => toggleTool(tool.slug, on === true)}
							/>
							<span class={cn('truncate', profileTools.has(tool.slug) && 'font-medium')}
								>{tool.label}</span
							>
						</label>
					{/each}
				</div>
			{/if}
			{#if unknownTools.length > 0}
				<ul class="flex flex-wrap gap-1" aria-label="Unknown tools">
					{#each unknownTools as slug (slug)}
						<li
							class="inline-flex items-center gap-1 rounded-full border border-destructive/40 bg-destructive/10 py-0.5 pl-2 pr-1 font-mono text-[11px] text-danger-fg"
						>
							{slug}
							<button
								type="button"
								class="flex size-4 cursor-pointer items-center justify-center rounded-full hover:bg-destructive/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
								aria-label="Remove {slug}"
								onclick={() => toggleTool(slug, false)}
							>
								<X class="size-3" />
							</button>
						</li>
					{/each}
				</ul>
			{/if}
			<p class="text-[11px] leading-4 text-muted-foreground">
				{#if ref && (restriction ?? []).length === 0}
					None selected: the profile runs unchanged.
				{:else}
					{ref
						? 'Restricts the profile to these tools (tools it lacks run with catalog defaults).'
						: 'Runs the research default configuration restricted to these tools.'} The AI reviewer runs
					only when it is listed.
				{/if}
			</p>
		{/if}
		{#if toolsProblem && !loading}
			<p class="text-[11px] text-danger-fg">{toolsProblem}</p>
		{/if}
	</div>

	<!-- Target -->
	<div class="space-y-1.5">
		<Label for={id('job')} class="text-xs">Generation job</Label>
		<Input
			id={id('job')}
			value={jobRef}
			placeholder={'{{params.generation_job_id}}'}
			autocomplete="off"
			spellcheck={false}
			class="h-8 font-mono text-xs"
			aria-describedby={id('job-help')}
			oninput={(e: Event) =>
				update({
					generation_job_id: (e.currentTarget as HTMLInputElement).value.trim() || undefined,
					generation_job: undefined
				})}
		/>
		{#if upstream.length > 0}
			<div class="flex flex-wrap gap-1">
				{#each upstream as step (step)}
					<Button
						variant="outline"
						size="sm"
						class="h-6 px-2 font-mono text-[11px]"
						title="Analyse the job produced by {step}"
						onclick={() =>
							update({
								generation_job_id: `{{steps.${step}.output.generation_job_id}}`,
								generation_job: undefined
							})}
					>
						{step}
					</Button>
				{/each}
			</div>
		{/if}
		<p id={id('job-help')} class="text-[11px] leading-4 text-muted-foreground">
			A job id or a reference such as <span class="break-all font-mono"
				>{'{{steps.generate.output.generation_job_id}}'}</span
			>. Empty: the pipeline's <span class="font-mono">generation_job_id</span> parameter.
		</p>
	</div>

	<!-- Gates -->
	<div class="space-y-2">
		<div class="flex items-baseline justify-between gap-2">
			<span class="text-xs font-medium">Finding thresholds</span>
			<span class="text-[11px] text-muted-foreground"
				>{thresholdCount ? `${thresholdCount} set` : 'none'}</span
			>
		</div>
		<div class="grid grid-cols-3 gap-2">
			{#each THRESHOLD_KEYS as key (key)}
				<div class="space-y-1">
					<Label for={id(`t-${key}`)} class="text-[11px] font-normal text-muted-foreground"
						>{thresholdLabels[key]}</Label
					>
					<NumberField
						id={id(`t-${key}`)}
						value={thresholds[key] ?? null}
						nullable
						integer
						min={0}
						placeholder="—"
						onchange={(n) => setThreshold(key, n)}
					/>
				</div>
			{/each}
		</div>
		<p class="text-[11px] leading-4 text-muted-foreground">
			Maximum counted static findings. Thresholds are evaluated only when the static measurement is
			available: a failed required tool never passes a gate.
		</p>
		<label class="flex cursor-pointer items-start gap-2 text-xs">
			<Checkbox
				class="mt-0.5"
				checked={config.fail_on_threshold === true}
				onCheckedChange={(on) => update({ fail_on_threshold: on === true ? true : undefined })}
			/>
			<span>Fail the step when a threshold is exceeded or cannot be evaluated</span>
		</label>
		<label class="flex cursor-pointer items-start gap-2 text-xs">
			<Checkbox
				class="mt-0.5"
				checked={config.fail_on_partial === true}
				onCheckedChange={(on) => update({ fail_on_partial: on === true ? true : undefined })}
			/>
			<span>Fail the step when the analysis is partial (a required tool did not succeed)</span>
		</label>
	</div>

	<!-- Timeout -->
	<div class="space-y-1.5">
		<Label for={id('timeout')} class="text-xs">Timeout</Label>
		<NumberField
			id={id('timeout')}
			value={timeout}
			nullable
			integer
			min={1}
			max={ANALYZE_MAX_TIMEOUT_SECONDS}
			unit="s"
			placeholder="{ANALYZE_DEFAULT_TIMEOUT_SECONDS} (4 h)"
			onchange={(n) => update({ timeout_seconds: n ?? undefined })}
		/>
		<p class="text-[11px] leading-4 text-muted-foreground">
			The step waits for the run; on timeout the run is cancelled and the step fails. At most {ANALYZE_MAX_TIMEOUT_SECONDS /
				3600} h.
		</p>
	</div>
</div>
