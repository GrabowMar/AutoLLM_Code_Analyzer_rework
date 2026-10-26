<script lang="ts">
	/**
	 * MethodEditor — the study's method: analysis profile (pick one, or
	 * customise it inline with the profile editor, which saves a new profile
	 * version), exclusion rules and the statistical analysis design.
	 *
	 * Props (bindable values are replaced, never mutated):
	 * - `profileId` (bindable): the pinned profile version;
	 * - `exclusionRules` (bindable `ExclusionRules`);
	 * - `design` (bindable `AnalysisDesign`);
	 * - `defaults`: `/profiles/defaults/` (outcomes, design vocabulary);
	 * - `errors`: server field errors (`profile_id`, `exclusion_rules.*`, `analysis_design.*`);
	 * - `busy` (bindable output): a profile save is in flight;
	 * - `disabled`; `onchange()` after every user edit.
	 */
	import { onMount } from 'svelte';
	import {
		createAnalysisProfile,
		getAnalysisProfile,
		listAnalysisProfiles,
		updateAnalysisProfile,
		type AnalysisDesign,
		type AnalysisProfile,
		type AnalysisProfileDetail,
		type Correction,
		type DesignFactor,
		type ExclusionRules,
		type ProfileConfig,
		type ProfileDefaults
	} from '$lib/api/analysis';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import {
		ProfileEditor,
		reviewerSummary,
		type ProfileValidationState
	} from '$lib/components/analysis/profile/index';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import X from '@lucide/svelte/icons/x';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import CheckList from './CheckList.svelte';
	import { CORRECTION_LABELS, DESIGN_FACTOR_LABELS, shortHash } from './format';

	interface Props {
		profileId?: string | null;
		exclusionRules: ExclusionRules;
		design: AnalysisDesign;
		defaults: ProfileDefaults | null;
		errors?: Record<string, string>;
		busy?: boolean;
		disabled?: boolean;
		onchange?: () => void;
	}

	let {
		profileId = $bindable(null),
		exclusionRules = $bindable(),
		design = $bindable(),
		defaults,
		errors = {},
		busy = $bindable(false),
		disabled = false,
		onchange
	}: Props = $props();

	// ── Profiles ─────────────────────────────────────────────────────────
	let profiles = $state<AnalysisProfile[]>([]);
	let profilesLoading = $state(true);
	let profilesError = $state('');
	let detail = $state<AnalysisProfileDetail | null>(null);
	let detailError = $state('');

	async function loadProfiles() {
		profilesLoading = true;
		profilesError = '';
		try {
			profiles = await listAnalysisProfiles();
		} catch (e) {
			profilesError = formatApiError(e, 'Could not load the analysis profiles.');
		} finally {
			profilesLoading = false;
		}
	}

	onMount(() => {
		void loadProfiles();
	});

	// The pinned version may not be the latest one the list shows.
	$effect(() => {
		const id = profileId;
		if (!id) {
			detail = null;
			return;
		}
		const ctrl = new AbortController();
		detailError = '';
		getAnalysisProfile(id, { signal: ctrl.signal })
			.then((d) => {
				if (!ctrl.signal.aborted) detail = d;
			})
			.catch((e) => {
				if (isAbortError(e) || ctrl.signal.aborted) return;
				detailError = formatApiError(e, 'Could not load the profile.');
			});
		return () => ctrl.abort();
	});

	const profileItems = $derived.by(() => {
		const items = profiles.map((p) => ({
			value: p.id,
			label: `${p.name} · v${p.version}${p.is_system ? ' · system' : ''}`,
			description: `${p.summary.tool_count} tools · ${reviewerSummary(p.summary.reviewer)}`
		}));
		// An older pinned version is listed explicitly.
		if (detail && !profiles.some((p) => p.id === detail!.id)) {
			items.unshift({
				value: detail.id,
				label: `${detail.name} · v${detail.version} (pinned)`,
				description: `latest is v${detail.latest_version}`
			});
		}
		return items;
	});

	const latestOfPinned = $derived(
		detail && detail.version < detail.latest_version
			? profiles.find((p) => p.slug === detail!.slug)
			: undefined
	);

	function pickProfile(id: string) {
		profileId = id || null;
		closeEditor();
		onchange?.();
	}

	// ── Inline customisation (saves a new profile version) ──────────────
	let editing = $state(false);
	let draft = $state<ProfileConfig | null>(null);
	let draftName = $state('');
	let validation = $state<ProfileValidationState>({
		status: 'idle',
		errors: {},
		contentHash: null,
		config: null,
		message: ''
	});
	let editorInvalid = $state(false);
	let saveError = $state('');
	let saveErrors = $state<Record<string, string>>({});

	const ownsProfile = $derived(!!detail && detail.is_owner && !detail.is_system);
	const unchanged = $derived(
		!!detail && validation.status === 'valid' && validation.contentHash === detail.content_hash
	);
	const canSave = $derived(
		!!draft &&
			!busy &&
			!editorInvalid &&
			validation.status === 'valid' &&
			!unchanged &&
			(ownsProfile || draftName.trim().length > 0)
	);

	function openEditor() {
		if (!detail) return;
		draft = JSON.parse(JSON.stringify(detail.config)) as ProfileConfig;
		draftName = `${detail.name} (customised)`.slice(0, 200);
		saveError = '';
		saveErrors = {};
		editing = true;
	}

	function closeEditor() {
		editing = false;
		draft = null;
		saveError = '';
		saveErrors = {};
	}

	async function saveCustomisation() {
		if (!detail || !draft || !canSave) return;
		busy = true;
		saveError = '';
		saveErrors = {};
		try {
			const saved = ownsProfile
				? await updateAnalysisProfile(detail.id, { config: draft })
				: await createAnalysisProfile({
						name: draftName.trim(),
						description: `Customised from ${detail.name} v${detail.version} for a study.`,
						config: draft
					});
			await loadProfiles();
			profileId = saved.id;
			closeEditor();
			onchange?.();
		} catch (e) {
			saveErrors = apiFieldErrors(e);
			saveError = formatApiError(e, 'Could not save the profile.');
		} finally {
			busy = false;
		}
	}

	// ── Exclusion rules ──────────────────────────────────────────────────
	type RuleFlag =
		'exclude_invalid' | 'exclude_empty_output' | 'exclude_truncated' | 'require_functional';
	const RULES: { key: RuleFlag; label: string; hint: string }[] = [
		{
			key: 'exclude_invalid',
			label: 'Exclude invalid generations',
			hint: 'Jobs flagged invalid by the generation validity checks.'
		},
		{
			key: 'exclude_empty_output',
			label: 'Exclude empty output',
			hint: 'The model wrote no source code (only scaffold/platform files).'
		},
		{
			key: 'exclude_truncated',
			label: 'Exclude truncated output',
			hint: 'The generation hit an output limit.'
		},
		{
			key: 'require_functional',
			label: 'Require a functional result',
			hint: 'Only jobs with a conformance pass rate (not "not evaluated").'
		}
	];

	function setRule(key: RuleFlag, value: boolean) {
		exclusionRules = { ...exclusionRules, [key]: value };
		onchange?.();
	}

	// ── Analysis design ──────────────────────────────────────────────────
	const factors = $derived<DesignFactor[]>(
		defaults?.design_choices.factors ?? ['model', 'condition', 'harness', 'mode', 'stack', 'app']
	);
	const corrections = $derived<Correction[]>(
		defaults?.design_choices.corrections ?? ['holm', 'bh', 'none']
	);
	const outcomeEntries = $derived(Object.entries(defaults?.outcomes ?? {}));

	function setDesign(patch: Partial<AnalysisDesign>) {
		design = { ...design, ...patch };
		onchange?.();
	}

	function setGroupBy(next: string[]) {
		const group_by = next as DesignFactor[];
		const block_by = design.block_by && group_by.includes(design.block_by) ? null : design.block_by;
		setDesign({ group_by, block_by });
	}

	const err = (key: string) => errors[key] ?? '';
</script>

<div class="space-y-4">
	<!-- Profile -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Analysis profile</Card.Title>
			<Card.Description>
				Tools, AI reviewer, scope and outcome settings. The exact version is pinned and frozen into
				the plan at lock.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			{#if profilesError}
				<ErrorAlert message={profilesError} onretry={loadProfiles} />
			{:else if profilesLoading && profiles.length === 0}
				<LoadingState label="loading profiles" class="py-6" />
			{:else}
				<div class="flex flex-wrap items-end gap-2">
					<div class="min-w-0 flex-1 space-y-1.5 sm:max-w-xl">
						<Label for="study-profile">Profile</Label>
						<Select.Simple
							id="study-profile"
							value={profileId ?? ''}
							items={profileItems}
							placeholder="Choose a profile…"
							onValueChange={pickProfile}
							aria-invalid={err('profile_id') ? true : undefined}
							disabled={disabled || editing}
						/>
					</div>
					{#if detail && !disabled && !editing}
						<Button variant="outline" size="sm" onclick={openEditor}>
							<SlidersHorizontal />Customize
						</Button>
					{/if}
					{#if detail}
						<Button
							variant="ghost"
							size="sm"
							href="/analysis/profiles/{detail.id}"
							target="_blank"
							rel="noopener"
						>
							<ExternalLink />Open
						</Button>
					{/if}
				</div>
				{#if err('profile_id')}<p class="text-[11px] text-danger-fg">
						{err('profile_id')}
					</p>{/if}
				{#if detailError}<ErrorAlert message={detailError} />{/if}

				{#if latestOfPinned && !editing}
					<div
						class="flex flex-wrap items-center gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning-fg"
					>
						Pinned v{detail?.version} · latest is v{detail?.latest_version}
						{#if !disabled}
							<Button
								variant="outline"
								size="sm"
								class="h-7"
								onclick={() => pickProfile(latestOfPinned.id)}>Use latest</Button
							>
						{/if}
					</div>
				{/if}

				{#if detail && !editing}
					<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2 lg:grid-cols-4">
						<div>
							<dt class="section-label">Tools</dt>
							<dd>
								{detail.summary.tool_count}
								<span class="text-muted-foreground"
									>({detail.config.tools.filter((t) => t.required).length} required)</span
								>
							</dd>
						</div>
						<div>
							<dt class="section-label">AI reviewer</dt>
							<dd>{reviewerSummary(detail.summary.reviewer)}</dd>
						</div>
						<div>
							<dt class="section-label">Measured origins</dt>
							<dd>{detail.config.scope.measured_origins.join(', ')}</dd>
						</div>
						<div>
							<dt class="section-label">Content hash</dt>
							<dd class="font-mono text-xs" title={detail.content_hash}>
								#{shortHash(detail.content_hash)}
							</dd>
						</div>
					</dl>
					<ul class="flex flex-wrap gap-1" aria-label="Tools in the profile">
						{#each detail.config.tools as tool (tool.slug)}
							<li
								class="mono-tag"
								title={tool.required
									? 'Required: a failure makes dependent outcomes NA'
									: 'Optional'}
							>
								{tool.slug}{#if tool.required}<span
										class="ml-0.5 text-primary"
										aria-label="required">*</span
									>{/if}
							</li>
						{/each}
					</ul>
				{/if}

				{#if editing && draft}
					<div class="space-y-4 rounded-md border border-primary/30 p-3">
						<div class="flex flex-wrap items-start justify-between gap-2">
							<div>
								<p class="text-sm font-semibold">Customise {detail?.name}</p>
								<p class="text-xs text-muted-foreground">
									{ownsProfile
										? `Saving creates version ${(detail?.latest_version ?? 0) + 1} of your profile; the study pins it.`
										: 'System profiles are read-only: saving creates a new profile of yours, which the study pins.'}
								</p>
							</div>
							<Button variant="ghost" size="sm" onclick={closeEditor} disabled={busy}
								><X />Discard</Button
							>
						</div>
						{#if !ownsProfile}
							<div class="max-w-md space-y-1.5">
								<Label for="custom-profile-name">New profile name</Label>
								<Input
									id="custom-profile-name"
									bind:value={draftName}
									maxlength={200}
									aria-invalid={saveErrors.name ? true : undefined}
								/>
								{#if saveErrors.name}<p class="text-[11px] text-danger-fg">
										{saveErrors.name}
									</p>{/if}
							</div>
						{/if}
						<ProfileEditor
							config={draft}
							onchange={(c) => (draft = c)}
							errors={saveErrors}
							{defaults}
							bind:validation
							bind:invalid={editorInvalid}
							idPrefix="study-profile"
						/>
						{#if saveError}<ErrorAlert message={saveError} />{/if}
						<div class="flex flex-wrap items-center justify-end gap-2">
							{#if unchanged}
								<span class="text-xs text-muted-foreground"
									>No change from v{detail?.version} yet.</span
								>
							{/if}
							<Button size="sm" onclick={saveCustomisation} disabled={!canSave}>
								{#if busy}<LoaderCircle
										class="animate-spin motion-reduce:animate-none"
									/>{:else}<Save />{/if}
								{ownsProfile ? 'Save new version' : 'Save as new profile'}
							</Button>
						</div>
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>

	<!-- Exclusion rules -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Exclusion rules</Card.Title>
			<Card.Description
				>Targets failing a rule stay listed as excluded (with the reason) and get no run.</Card.Description
			>
		</Card.Header>
		<Card.Content class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]">
			<fieldset class="space-y-2" {disabled}>
				<legend class="sr-only">Exclusion rules</legend>
				{#each RULES as rule (rule.key)}
					<label class="flex cursor-pointer items-start gap-2.5 text-sm">
						<Checkbox
							class="mt-0.5"
							checked={exclusionRules[rule.key]}
							onCheckedChange={(on) => setRule(rule.key, on === true)}
							{disabled}
						/>
						<span>
							<span class="font-medium">{rule.label}</span>
							<span class="block text-xs text-muted-foreground">{rule.hint}</span>
						</span>
					</label>
				{/each}
			</fieldset>
			<NumberField
				id="rule-min-sloc"
				label="Minimum measured SLOC"
				value={exclusionRules.min_measured_sloc}
				min={0}
				max={1000000}
				required
				hint="Targets with less model-written code are excluded (0 = no minimum)."
				error={err('exclusion_rules.min_measured_sloc')}
				onchange={(v) => {
					if (v !== null) {
						exclusionRules = { ...exclusionRules, min_measured_sloc: v };
						onchange?.();
					}
				}}
				{disabled}
			/>
		</Card.Content>
	</Card.Root>

	<!-- Analysis design -->
	<Card.Root>
		<Card.Header>
			<Card.Title>Analysis design</Card.Title>
			<Card.Description>
				How runs are grouped and compared. Blocking pairs groups within each block (Friedman /
				Wilcoxon) when the design is complete; otherwise independent tests (Kruskal-Wallis /
				Mann-Whitney) are used.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-5">
			<div class="grid gap-5 lg:grid-cols-2">
				<div class="space-y-1">
					<CheckList
						legend="Group by"
						columns={3}
						items={factors.map((f) => ({
							value: f,
							label: DESIGN_FACTOR_LABELS[f] ?? f
						}))}
						selected={design.group_by}
						onchange={setGroupBy}
						{disabled}
					/>
					<p class="text-[11px] text-muted-foreground">
						Several factors are crossed (e.g. model × harness).
					</p>
					{#if err('analysis_design.group_by')}<p class="text-[11px] text-danger-fg">
							{err('analysis_design.group_by')}
						</p>{/if}
				</div>
				<div class="space-y-4">
					<div class="space-y-1.5">
						<Label for="design-block">Block by</Label>
						<Select.Simple
							id="design-block"
							value={design.block_by ?? ''}
							clearLabel="No blocking (independent groups)"
							placeholder="No blocking (independent groups)"
							items={factors
								.filter((f) => !design.group_by.includes(f))
								.map((f) => ({
									value: f,
									label: DESIGN_FACTOR_LABELS[f] ?? f
								}))}
							onValueChange={(v) => setDesign({ block_by: (v || null) as DesignFactor | null })}
							aria-invalid={err('analysis_design.block_by') ? true : undefined}
							{disabled}
						/>
						{#if err('analysis_design.block_by')}<p class="text-[11px] text-danger-fg">
								{err('analysis_design.block_by')}
							</p>{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="design-primary">Primary outcome</Label>
						<Select.Simple
							id="design-primary"
							value={design.primary_outcome}
							items={outcomeEntries.map(([key, meta]) => ({
								value: key,
								label: meta.label,
								description: [
									meta.unit,
									meta.higher_is_better === false
										? 'lower is better'
										: meta.higher_is_better
											? 'higher is better'
											: ''
								]
									.filter(Boolean)
									.join(' · ')
							}))}
							onValueChange={(v) => setDesign({ primary_outcome: v })}
							aria-invalid={err('analysis_design.primary_outcome') ? true : undefined}
							{disabled}
						/>
						{#if err('analysis_design.primary_outcome')}
							<p class="text-[11px] text-danger-fg">
								{err('analysis_design.primary_outcome')}
							</p>
						{:else if defaults?.outcomes[design.primary_outcome]}
							<p class="text-[11px] text-muted-foreground">
								{defaults.outcomes[design.primary_outcome].description}
							</p>
						{/if}
					</div>
				</div>
			</div>

			<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
				<NumberField
					id="design-alpha"
					label="Significance level α"
					value={design.alpha}
					min={0.0001}
					max={0.4999}
					integer={false}
					step={0.01}
					required
					error={err('analysis_design.alpha')}
					onchange={(v) => v !== null && setDesign({ alpha: v })}
					{disabled}
				/>
				<div class="space-y-1.5">
					<Label class="text-xs" for="design-correction">Confirmatory correction</Label>
					<Select.Simple
						id="design-correction"
						size="sm"
						value={design.correction}
						items={corrections.map((c) => ({
							value: c,
							label: CORRECTION_LABELS[c] ?? c
						}))}
						onValueChange={(v) => setDesign({ correction: v as Correction })}
						{disabled}
					/>
				</div>
				<div class="space-y-1.5">
					<Label class="text-xs" for="design-exploratory">Exploratory correction</Label>
					<Select.Simple
						id="design-exploratory"
						size="sm"
						value={design.exploratory_correction}
						items={corrections.map((c) => ({
							value: c,
							label: CORRECTION_LABELS[c] ?? c
						}))}
						onValueChange={(v) => setDesign({ exploratory_correction: v as Correction })}
						{disabled}
					/>
				</div>
				<div class="space-y-1.5">
					<span class="block text-xs font-medium" id="design-aggregate-label"
						>Replicates per cell</span
					>
					<Segmented
						size="sm"
						aria-labelledby="design-aggregate-label"
						value={design.cell_aggregate}
						items={[
							{
								value: 'mean',
								label: 'Mean',
								title: 'Aggregate replicates of a group × block cell by their mean'
							},
							{
								value: 'median',
								label: 'Median',
								title: 'Aggregate replicates by their median'
							}
						]}
						onValueChange={(v) =>
							setDesign({
								cell_aggregate: v as AnalysisDesign['cell_aggregate']
							})}
						{disabled}
					/>
				</div>
			</div>

			<details class="group rounded-md border border-border">
				<summary
					class="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium"
				>
					<ChevronRight
						class="h-4 w-4 transition-transform duration-150 group-open:rotate-90"
						aria-hidden="true"
					/>
					Advanced: outcomes, intervals, resampling
				</summary>
				<div class="space-y-4 border-t border-border p-3">
					<CheckList
						legend="Outcomes analysed"
						columns={3}
						items={outcomeEntries.map(([key, meta]) => ({
							value: key,
							label: meta.label
						}))}
						selected={design.outcomes ?? []}
						emptyMeans="every outcome is analysed"
						onchange={(next) => setDesign({ outcomes: next.length ? next : null })}
						{disabled}
					/>
					{#if err('analysis_design.outcomes')}<p class="text-[11px] text-danger-fg">
							{err('analysis_design.outcomes')}
						</p>{/if}
					<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
						<NumberField
							id="design-ci"
							label="CI level"
							value={design.ci_level}
							min={0.5001}
							max={0.9999}
							integer={false}
							step={0.01}
							required
							error={err('analysis_design.ci_level')}
							onchange={(v) => v !== null && setDesign({ ci_level: v })}
							{disabled}
						/>
						<NumberField
							id="design-bootstrap"
							label="Bootstrap resamples"
							value={design.bootstrap_resamples}
							min={0}
							max={100000}
							required
							error={err('analysis_design.bootstrap_resamples')}
							onchange={(v) => v !== null && setDesign({ bootstrap_resamples: v })}
							{disabled}
						/>
						<NumberField
							id="design-effect-ci"
							label="Effect-size CI resamples"
							value={design.effect_ci_resamples}
							min={0}
							max={100000}
							required
							error={err('analysis_design.effect_ci_resamples')}
							onchange={(v) => v !== null && setDesign({ effect_ci_resamples: v })}
							{disabled}
						/>
						<NumberField
							id="design-seed"
							label="Random seed"
							value={design.seed}
							min={0}
							required
							hint="Fixes the bootstrap; the report is reproducible."
							error={err('analysis_design.seed')}
							onchange={(v) => v !== null && setDesign({ seed: v })}
							{disabled}
						/>
					</div>
				</div>
			</details>
			{#each Object.entries(errors).filter(([k]) => k.startsWith('analysis_design') && !['alpha', 'block_by', 'group_by', 'primary_outcome', 'outcomes', 'ci_level', 'bootstrap_resamples', 'effect_ci_resamples', 'seed'].some((f) => k === `analysis_design.${f}`)) as [k, msg] (k)}
				<p class="text-[11px] text-danger-fg">{k}: {msg}</p>
			{/each}
		</Card.Content>
	</Card.Root>
</div>
