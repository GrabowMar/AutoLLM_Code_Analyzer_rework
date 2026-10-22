<script lang="ts">
	import { page } from '$app/state';
	import { onMount, untrack } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import * as Select from '$lib/components/ui/select';
	import { toneClasses, statusTone } from '$lib/constants/colors';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import {
		LLMParamsEditor,
		ModelPicker,
		PresetPicker,
		ProfilePicker,
		cleanLLMParams,
		harnessOptionChips,
		loadAppSpecCatalog,
		loadModelCatalog,
		summarizeBudget,
		summarizeLLMParams,
		summarizePreset,
	} from '$lib/components/generator';
	import { cn } from '$lib/utils';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import type { LLMModelSummary } from '$lib/api/models';
	import {
		getAgentConfig,
		getGenerationProfiles,
		previewEffectiveLLMParams,
		type AgentConfigInfo,
		type AppRequirementTemplate,
		type GenerationProfile,
		type HarnessPresetConfig,
		type LLMParams,
		type PresetKind,
		type RunPreset,
		type SamplingPresetConfig,
	} from '$lib/api/generation';
	import {
		getExperiment,
		listConditions,
		createCondition,
		deleteCondition,
		previewExperiment,
		launchExperiment,
		getExperimentStatus,
		exportExperiment,
		archiveExperiment,
		type CreateConditionPayload,
		type Experiment,
		type ExperimentBatch,
		type ExperimentComparabilityRow,
		type ExperimentCondition,
		type ExperimentLaunchError,
		type ExperimentPreview,
		type ExperimentStatusReport,
	} from '$lib/api/experiments';
	import { presetHarness, resolvePreset } from '$lib/components/templates/presets';
	import Archive from '@lucide/svelte/icons/archive';
	import Calculator from '@lucide/svelte/icons/calculator';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Download from '@lucide/svelte/icons/download';
	import GitCompareArrows from '@lucide/svelte/icons/git-compare-arrows';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import Rocket from '@lucide/svelte/icons/rocket';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';

	const POLL_MS = 10_000;
	const LIVE_RUN_STATUSES = new Set(['pending', 'queued', 'running']);

	const experimentId = $derived(page.params.id!);

	let experiment = $state<Experiment | null>(null);
	let conditions = $state<ExperimentCondition[]>([]);
	let loading = $state(true);
	let loadError = $state('');
	let error = $state('');

	// Catalogs for labels and the condition form (failures only degrade labels).
	let agentConfig = $state<AgentConfigInfo | null>(null);
	let profiles = $state<GenerationProfile[]>([]);
	let profilesLoaded = $state(false);
	let models = $state<LLMModelSummary[]>([]);
	let appSpecs = $state<AppRequirementTemplate[]>([]);
	let presetById = $state<Record<number, RunPreset>>({});
	const requestedPresets = new Set<number>();

	const isAgent = $derived(experiment?.mode === 'agent');
	const isDraft = $derived(experiment?.status === 'draft');

	// ── loading ────────────────────────────────────────────────────
	let loadToken = 0;

	async function load(id: string) {
		const token = ++loadToken;
		loading = true;
		loadError = '';
		error = '';
		previewResult = null;
		launchedBatch = null;
		launchErrors = [];
		launchError = '';
		status = null;
		try {
			const [exp, conds] = await Promise.all([getExperiment(id), listConditions(id)]);
			if (token !== loadToken) return;
			experiment = exp;
			conditions = conds;
			if (exp.status !== 'draft') void refreshStatus();
		} catch (e) {
			if (token === loadToken) loadError = formatApiError(e, 'Failed to load the experiment.');
		} finally {
			if (token === loadToken) loading = false;
		}
	}

	// Reload when navigating between experiments (the component is reused).
	$effect(() => {
		const id = experimentId;
		untrack(() => load(id));
	});

	onMount(() => {
		getAgentConfig()
			.then((c) => (agentConfig = c))
			.catch(() => {});
		getGenerationProfiles()
			.then((list) => {
				profiles = list;
				profilesLoaded = true;
			})
			.catch(() => {});
		loadModelCatalog()
			.then((list) => (models = list))
			.catch(() => {});
		loadAppSpecCatalog()
			.then((list) => (appSpecs = list))
			.catch(() => {});
	});

	// Preset labels: experiment-wide presets plus every condition's harness preset.
	const wantedPresets = $derived.by(() => {
		const list: [PresetKind, number][] = [];
		if (experiment?.sampling_preset != null) list.push(['sampling', experiment.sampling_preset]);
		if (experiment?.budget_preset != null) list.push(['budget', experiment.budget_preset]);
		for (const c of conditions) if (c.harness_preset != null) list.push(['harness', c.harness_preset]);
		if (newHarnessPresetId != null) list.push(['harness', newHarnessPresetId]);
		return list;
	});

	$effect(() => {
		for (const [kind, id] of wantedPresets) {
			if (requestedPresets.has(id)) continue;
			requestedPresets.add(id);
			resolvePreset(kind, id).then((preset) => {
				if (preset) presetById = { ...presetById, [id]: preset };
				else requestedPresets.delete(id);
			});
		}
	});

	// ── labels ─────────────────────────────────────────────────────
	const appName = $derived(new Map(appSpecs.map((a) => [a.id, a.name])));
	const defaultHarness = $derived(agentConfig?.harnesses.find((h) => h.is_default));

	function harnessInfo(name: string) {
		return agentConfig?.harnesses.find((h) => h.name === name);
	}

	function presetLabel(id: number | null | undefined, ref?: { name: string; version: number } | null): string {
		if (id == null) return '';
		const preset = presetById[id] ?? ref;
		return preset ? `${preset.name} v${preset.version}` : `preset #${id}`;
	}

	/** "Name v2 · temperature 0 · …" once the preset's config is loaded. */
	function presetWithSummary(id: number, ref?: { name: string; version: number } | null): string {
		const preset = presetById[id];
		const summary = preset ? summarizePreset(preset) : '';
		return summary ? `${presetLabel(id, ref)} · ${summary}` : presetLabel(id, ref);
	}

	function conditionHarnessLabel(cond: ExperimentCondition): string {
		if (cond.harness_preset != null) return presetLabel(cond.harness_preset, cond.harness_preset_ref);
		if (cond.harness) return harnessInfo(cond.harness)?.label ?? cond.harness;
		return defaultHarness ? `${defaultHarness.label} (default)` : 'default harness';
	}

	function conditionChips(cond: ExperimentCondition): string[] {
		const preset = cond.harness_preset != null ? presetById[cond.harness_preset] : undefined;
		if (!preset) return [];
		const config = preset.config as HarnessPresetConfig;
		return harnessOptionChips(config.options, harnessInfo(config.harness)?.option_schema ?? []);
	}

	function conditionLabel(cond: ExperimentCondition): string {
		const parts = [cond.model_name ?? `model #${cond.model}`];
		if (isAgent) parts.push(conditionHarnessLabel(cond));
		if (cond.bundle_slug) parts.push(`${cond.bundle_slug}@v${cond.bundle_version}`);
		return cond.label || parts.join(' × ');
	}

	const conditionById = $derived(new Map(conditions.map((c) => [c.id, c])));
	const samplingPreset = $derived(experiment?.sampling_preset != null ? presetById[experiment.sampling_preset] : undefined);

	// ── add condition ──────────────────────────────────────────────
	let newModelId = $state<number | null>(null);
	let newHarnessPresetId = $state<number | null>(null);
	let newProfileId = $state<number | null>(null);
	let showProfile = $state(false);
	let newOverrides = $state<LLMParams>({});
	let overridesInvalid = $state(false);
	let showOverrides = $state(false);
	let addingCondition = $state(false);
	let addError = $state('');

	const newHarnessName = $derived.by(() => {
		const preset = newHarnessPresetId != null ? presetById[newHarnessPresetId] : undefined;
		return preset ? presetHarness(preset) : '';
	});
	// Unknown harness (catalog not loaded yet) counts as needing tools, the safe side.
	const requireTools = $derived(isAgent && (harnessInfo(newHarnessName)?.requires_tool_calling ?? true));
	const selectedModel = $derived(models.find((m) => m.id === newModelId));
	const modelIneligible = $derived(requireTools && !!selectedModel && !selectedModel.supports_function_calling);
	const conditionProfileId = $derived(isAgent ? (showProfile ? newProfileId : null) : newProfileId);

	const duplicateCondition = $derived(
		newModelId != null &&
			conditions.some(
				(c) =>
					c.model === newModelId &&
					(c.profile ?? null) === conditionProfileId &&
					(isAgent ? (c.harness_preset ?? null) === newHarnessPresetId : true)
			)
	);

	const addBlocker = $derived.by(() => {
		if (newModelId == null) return 'Pick a model.';
		if (isAgent && newHarnessPresetId == null) return 'Pick a harness preset.';
		if (!isAgent && newProfileId == null) return 'Pick a specific profile: a condition pins one profile version.';
		if (modelIneligible) return 'This harness needs a model with tool calling.';
		if (overridesInvalid) return 'Fix the highlighted override fields.';
		if (duplicateCondition) return 'This experiment already has that condition.';
		return '';
	});

	// Scaffolding: the server resolves mode default → profile → preset → experiment
	// overrides ("inline" there is this experiment's llm_defaults).
	let scaffoldingInherited = $state<{ values: LLMParams; sources: Record<string, string> } | null>(null);
	$effect(() => {
		if (!experiment || isAgent || !showOverrides) return;
		const body = {
			mode: 'scaffolding' as const,
			profile_id: conditionProfileId,
			sampling_preset_id: experiment.sampling_preset ?? null,
			llm_params: $state.snapshot(experiment.llm_defaults ?? {}) as LLMParams,
		};
		const controller = new AbortController();
		previewEffectiveLLMParams(body, { signal: controller.signal })
			.then((result) => {
				const sources = Object.fromEntries(
					Object.entries(result.sources).map(([key, source]) => [key, source === 'inline' ? 'llm_defaults' : source])
				);
				scaffoldingInherited = { values: result.effective, sources };
			})
			.catch((e: unknown) => {
				if (!isAbortError(e)) scaffoldingInherited = null;
			});
		return () => controller.abort();
	});

	// What the per-condition overrides sit on: profile → sampling preset → experiment overrides.
	const overridesInherited = $derived.by(() => {
		if (!isAgent && scaffoldingInherited) return scaffoldingInherited;
		const values: Record<string, unknown> = {};
		const sources: Record<string, string> = {};
		const layer = (params: LLMParams | undefined, source: string) => {
			for (const [key, value] of Object.entries(params ?? {})) {
				values[key] = value;
				sources[key] = source;
			}
		};
		const profile = profiles.find((p) => p.id === conditionProfileId);
		layer(profile?.llm_config, 'profile');
		layer((samplingPreset?.config as SamplingPresetConfig | undefined)?.llm_params, 'preset');
		layer(experiment?.llm_defaults, 'llm_defaults');
		return { values: values as LLMParams, sources };
	});

	const agentProfileItems = $derived(
		profiles.map((p) => {
			const otherStack = !!experiment?.stack_slug && p.scaffolding_slug !== experiment.stack_slug;
			return {
				value: String(p.id),
				label: `${p.name} v${p.version}`,
				description: otherStack
					? `${p.scaffolding_slug} — not this experiment's stack`
					: `${p.scaffolding_slug} · ${summarizeLLMParams(p.llm_config) || 'no LLM defaults'}`,
				disabled: otherStack,
			};
		})
	);

	async function addCondition() {
		if (addBlocker || !experiment || newModelId == null) return;
		addingCondition = true;
		addError = '';
		const overrides = cleanLLMParams($state.snapshot(newOverrides) as LLMParams);
		if (isAgent) {
			delete overrides.stop;
			delete overrides.response_format;
		}
		const payload: CreateConditionPayload = { model_id: newModelId, param_overrides: overrides };
		if (isAgent) payload.harness_preset_id = newHarnessPresetId;
		if (conditionProfileId != null) payload.profile_id = conditionProfileId;
		try {
			const cond = await createCondition(experimentId, payload);
			conditions = [...conditions, cond];
			launchErrors = [];
			// Keep the harness preset and profile: the next condition usually varies the model.
			newModelId = null;
			newOverrides = {};
			showOverrides = false;
			previewResult = null;
		} catch (e) {
			addError = formatApiError(e, 'Failed to add the condition.');
		} finally {
			addingCondition = false;
		}
	}

	async function removeCondition(cond: ExperimentCondition) {
		error = '';
		try {
			await deleteCondition(experimentId, cond.id);
			conditions = conditions.filter((c) => c.id !== cond.id);
			previewResult = null;
			launchErrors = [];
		} catch (e) {
			error = formatApiError(e, 'Failed to remove the condition.');
		}
	}

	// ── preview & launch ───────────────────────────────────────────
	let previewing = $state(false);
	let previewResult = $state<ExperimentPreview | null>(null);
	let launching = $state(false);
	let launchError = $state('');
	let launchErrors = $state<ExperimentLaunchError[]>([]);
	let launchConfirmOpen = $state(false);
	let launchedBatch = $state<ExperimentBatch | null>(null);

	// Blocking problems from the latest preview (or a refused launch): the
	// backend refuses the whole launch while any remain.
	const blockingErrors = $derived(launchErrors.length ? launchErrors : (previewResult?.launch_errors ?? []));

	async function runPreview(): Promise<boolean> {
		previewing = true;
		error = '';
		launchErrors = [];
		try {
			previewResult = await previewExperiment(experimentId);
			return true;
		} catch (e) {
			error = formatApiError(e, 'Failed to preview the experiment.');
			return false;
		} finally {
			previewing = false;
		}
	}

	/** Launch (draft) or top up failed / cancelled cells (running, completed): preview first, then confirm. */
	async function askLaunch() {
		launchError = '';
		if (!isDraft || !previewResult) {
			if (!(await runPreview())) return;
		}
		if (blockingErrors.length > 0) return;
		if (previewResult && (previewResult.pending_jobs ?? previewResult.total_jobs) === 0) {
			launchError = 'Every cell already has a run; there is nothing to launch.';
			return;
		}
		launchConfirmOpen = true;
	}

	async function doLaunch() {
		launching = true;
		launchError = '';
		launchErrors = [];
		try {
			launchedBatch = await launchExperiment(experimentId);
			previewResult = null;
			experiment = await getExperiment(experimentId);
			await refreshStatus();
		} catch (e) {
			const body = e as { errors?: unknown } | null;
			if (body && Array.isArray(body.errors) && body.errors.length > 0) {
				launchErrors = body.errors as ExperimentLaunchError[];
			} else {
				launchError = formatApiError(e, 'Failed to launch the experiment.');
			}
		} finally {
			launching = false;
			launchConfirmOpen = false;
		}
	}

	// ── archive / export ───────────────────────────────────────────
	let archiveConfirmOpen = $state(false);
	let archiving = $state(false);

	async function doArchive() {
		archiving = true;
		error = '';
		try {
			experiment = await archiveExperiment(experimentId);
		} catch (e) {
			error = formatApiError(e, 'Failed to archive the experiment.');
		} finally {
			archiving = false;
			archiveConfirmOpen = false;
		}
	}

	let exporting = $state(false);

	async function doExport() {
		exporting = true;
		error = '';
		try {
			const data = await exportExperiment(experimentId);
			const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
			const url = URL.createObjectURL(blob);
			const a = document.createElement('a');
			a.href = url;
			a.download = `experiment-${experiment?.slug ?? experimentId}.json`;
			a.click();
			URL.revokeObjectURL(url);
		} catch (e) {
			error = formatApiError(e, 'Export failed.');
		} finally {
			exporting = false;
		}
	}

	// ── progress ───────────────────────────────────────────────────
	let status = $state<ExperimentStatusReport | null>(null);
	let statusLoading = $state(false);
	let statusError = $state('');

	let statusInFlight = false;

	/** `silent` (polling) keeps the button and grid as they are while it loads. */
	async function refreshStatus(silent = false) {
		if (statusInFlight) return;
		const id = experimentId;
		statusInFlight = true;
		if (!silent) statusLoading = true;
		try {
			const next = await getExperimentStatus(id);
			if (id !== experimentId) return;
			status = next;
			statusError = '';
		} catch (e) {
			// A failed refresh keeps the last grid on screen.
			statusError = formatApiError(e, 'Could not refresh the progress.');
		} finally {
			statusInFlight = false;
			statusLoading = false;
		}
	}

	const hasLiveRuns = $derived(
		!!status && status.grid.some((cell) => cell.runs.some((run) => LIVE_RUN_STATUSES.has(run.status)))
	);

	$effect(() => {
		if (!hasLiveRuns) return;
		const timer = setInterval(() => {
			if (document.visibilityState === 'visible') void refreshStatus(true);
		}, POLL_MS);
		return () => clearInterval(timer);
	});

	const progressGroups = $derived.by(() => {
		if (!status) return [];
		const byCondition = new Map<number, ExperimentStatusReport['grid']>();
		for (const cell of status.grid) {
			const list = byCondition.get(cell.condition_id) ?? [];
			list.push(cell);
			byCondition.set(cell.condition_id, list);
		}
		const ordered = [
			...conditions.map((c) => c.id).filter((id) => byCondition.has(id)),
			...[...byCondition.keys()].filter((id) => !conditionById.has(id)),
		];
		return ordered.map((id) => ({
			id,
			label: conditionById.has(id) ? conditionLabel(conditionById.get(id)!) : `Condition #${id}`,
			cells: [...(byCondition.get(id) ?? [])].sort((a, b) =>
				(appName.get(a.app_id) ?? '').localeCompare(appName.get(b.app_id) ?? '')
			),
		}));
	});

	// ── comparability ──────────────────────────────────────────────
	// Older backends sent fewer keys (and `stack` for the stack); every key is optional here.
	type CompRow = Pick<ExperimentComparabilityRow, 'condition_id' | 'model' | 'problems'> &
		Partial<Omit<ExperimentComparabilityRow, 'condition_id' | 'model' | 'problems'>> & { stack?: string | null };

	interface CompColumn {
		id: string;
		label: string;
		keys: string[];
		cell: (row: CompRow) => string;
	}

	function formatParams(params: Record<string, unknown> | null | undefined): string {
		const entries = Object.entries(params ?? {});
		return entries.length ? entries.map(([k, v]) => `${k}=${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' ') : '—';
	}

	const compRows = $derived((previewResult?.comparability?.conditions ?? []) as CompRow[]);
	const differsOn = $derived(new Set(previewResult?.comparability?.differs_on ?? []));
	const compColumns = $derived.by((): CompColumn[] => {
		const rows = compRows;
		const has = (key: keyof CompRow) => rows.some((r) => r[key] !== undefined && r[key] !== null);
		const columns: CompColumn[] = [
			{
				id: 'harness',
				label: 'Harness',
				keys: ['harness', 'harness_version', 'harness_options'],
				cell: (r) => {
					const options = Object.keys(r.harness_options ?? {}).length ? ` ${formatParams(r.harness_options)}` : '';
					return `${r.harness ?? '—'}@${r.harness_version ?? '?'}${options}`;
				},
			},
			{
				id: 'sampling',
				label: 'Sampling',
				keys: ['sampling_policy'],
				cell: (r) => (r.sampling_policy ?? '—').replace(/_/g, ' '),
			},
			{ id: 'forced', label: 'Forced params', keys: ['forced_params'], cell: (r) => formatParams(r.forced_params) },
		];
		if (has('output_cap')) {
			columns.push({
				id: 'cap',
				label: 'Output cap',
				keys: ['output_cap'],
				cell: (r) => (r.output_cap != null ? r.output_cap.toLocaleString() : '—'),
			});
		}
		// The stack is experiment-wide; its column only matters for legacy (per-profile) stacks.
		if ((has('stack_slug') || has('stack')) && (!experiment?.stack_slug || differsOn.has('stack_slug'))) {
			columns.push({ id: 'stack', label: 'Stack', keys: ['stack', 'stack_slug'], cell: (r) => r.stack_slug ?? r.stack ?? '—' });
		}
		if (has('profile')) {
			columns.push({ id: 'profile', label: 'Profile', keys: ['profile'], cell: (r) => r.profile ?? '—' });
		}
		if (rows.some((r) => r.provider_routing != null && r.provider_routing !== 'default') || differsOn.has('provider_routing')) {
			columns.push({
				id: 'routing',
				label: 'Provider routing',
				keys: ['provider_routing'],
				cell: (r) =>
					r.provider_routing == null || r.provider_routing === 'default' ? 'default' : formatParams(r.provider_routing),
			});
		}
		if (rows.some((r) => Object.keys(r.param_overrides ?? {}).length > 0)) {
			columns.push({ id: 'overrides', label: 'Overrides', keys: ['param_overrides'], cell: (r) => formatParams(r.param_overrides) });
		}
		columns.push({
			id: 'seed',
			label: 'Seed',
			keys: ['seed_forwarded'],
			cell: (r) => (r.seed_forwarded ? 'forwarded' : 'not supported'),
		});
		return columns;
	});

	function columnDiffers(column: CompColumn): boolean {
		return column.keys.some((k) => differsOn.has(k));
	}

	function compRowLabel(row: CompRow): string {
		const cond = conditionById.get(row.condition_id);
		const preset = row.harness_preset?.name ?? (cond?.harness_preset != null ? presetLabel(cond.harness_preset) : '');
		return preset ? `${row.model} × ${preset}` : row.model;
	}

	/** Dollar amounts: cents for caps and totals, more digits only for sub-cent costs. */
	function formatUsd(value: number): string {
		if (value === 0) return '$0.00';
		return value < 0.01 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
	}

	function previewCost(preview: ExperimentPreview): string {
		return formatUsd(preview.estimated_cost);
	}

	function plural(n: number, one: string, many = `${one}s`): string {
		return n === 1 ? one : many;
	}

	function errorConditionLabel(err: ExperimentLaunchError): string {
		const cond = conditionById.get(err.condition_id);
		return cond ? conditionLabel(cond) : `Condition #${err.condition_id}`;
	}

	const launchRuns = $derived(previewResult ? (previewResult.pending_jobs ?? previewResult.total_jobs) : 0);
	const failedRuns = $derived((status?.by_status.failed ?? 0) + (status?.by_status.cancelled ?? 0));

	const designItems = $derived.by(() => {
		if (!experiment) return [];
		const items: { label: string; value: string; mono?: boolean }[] = [
			{ label: 'Method', value: isAgent ? 'Agent' : 'Template scaffolding' },
			{ label: 'Stack', value: experiment.stack_slug || 'each condition profile’s stack', mono: !!experiment.stack_slug },
			{
				label: 'Apps',
				value: experiment.app_requirement_ids.length
					? experiment.app_requirement_ids.map((id) => appName.get(id) ?? `#${id}`).join(', ')
					: 'none',
			},
			{ label: 'Repeats', value: `${experiment.repeats}× per cell` },
			{
				label: 'Seed',
				value: experiment.base_seed != null ? String(experiment.base_seed) : 'random per run',
				mono: experiment.base_seed != null,
			},
			{
				label: 'Sampling preset',
				value:
					experiment.sampling_preset != null
						? presetWithSummary(experiment.sampling_preset, experiment.sampling_preset_ref)
						: isAgent
							? 'Provider defaults (platform default)'
							: 'none (template mode defaults)',
			},
			{
				label: 'Experiment overrides',
				value: summarizeLLMParams(experiment.llm_defaults) || 'none',
				mono: !!summarizeLLMParams(experiment.llm_defaults),
			},
		];
		if (isAgent) {
			items.push(
				{
					label: 'Budget preset',
					value:
						experiment.budget_preset != null
							? presetWithSummary(experiment.budget_preset, experiment.budget_preset_ref)
							: 'Platform defaults',
				},
				{ label: 'Budget overrides', value: summarizeBudget(experiment.agent_config) || 'none' }
			);
			// Experiments from before presets may carry these experiment-wide.
			const legacy = [
				experiment.agent_config?.harness ? `harness ${experiment.agent_config.harness}` : '',
				experiment.agent_config?.sampling_policy ? `sampling ${experiment.agent_config.sampling_policy.replace(/_/g, ' ')}` : '',
			].filter(Boolean);
			if (legacy.length) items.push({ label: 'Legacy settings', value: legacy.join(' · ') });
		}
		return items;
	});
</script>

{#snippet blockingList()}
	{#if blockingErrors.length > 0}
		<Alert variant="destructive">
			<CircleAlert />
			<div class="min-w-0 space-y-1">
				<p class="font-medium">
					{#if launchErrors.length > 0}
						The launch was refused; nothing was created.
					{:else}
						{blockingErrors.length === 1 ? 'One problem blocks' : `${blockingErrors.length} problems block`} the launch.
					{/if}
				</p>
				<AlertDescription>
					<ul class="list-disc space-y-0.5 pl-4 text-xs">
						{#each blockingErrors as err, i (i)}
							<li><span class="font-medium">{errorConditionLabel(err)}:</span> {err.detail}</li>
						{/each}
					</ul>
				</AlertDescription>
			</div>
		</Alert>
	{/if}
{/snippet}

<div class="mx-auto w-full max-w-5xl space-y-6">
	<PageHeader
		title={experiment?.name ?? 'Experiment'}
		description={experiment?.description || undefined}
		back={{ href: '/experiments', label: 'Experiments' }}
	>
		{#snippet meta()}
			{#if experiment}
				<StatusBadge status={experiment.status} />
				<Badge variant="outline">{isAgent ? 'Agent' : 'Template scaffolding'}</Badge>
			{/if}
		{/snippet}
		{#snippet actions()}
			{#if experiment}
				<Button variant="outline" size="sm" onclick={doExport} disabled={exporting}>
					{#if exporting}<LoaderCircle class="animate-spin" />{:else}<Download />{/if}
					Export
				</Button>
				{#if experiment.status !== 'archived'}
					<Button variant="outline" size="sm" onclick={() => (archiveConfirmOpen = true)}>
						<Archive />
						Archive
					</Button>
				{/if}
			{/if}
		{/snippet}
		{#if experiment?.hypothesis}
			<p class="text-sm italic text-muted-foreground">“{experiment.hypothesis}”</p>
		{/if}
	</PageHeader>

	{#if loading && !experiment}
		<LoadingState label="loading experiment" />
	{:else if loadError}
		<ErrorAlert message={loadError} onretry={() => load(experimentId)} />
	{:else if experiment}
		{#if error}
			<ErrorAlert message={error} />
		{/if}

		<Card.Root>
			<Card.Header>
				<Card.Title>Design</Card.Title>
				<Card.Description>Shared by every condition; a condition only varies what its row below says.</Card.Description>
			</Card.Header>
			<Card.Content>
				<dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
					{#each designItems as item (item.label)}
						<div class="min-w-0">
							<dt class="section-label">{item.label}</dt>
							<dd class={cn('break-words', item.mono && 'font-mono text-xs')}>{item.value}</dd>
						</div>
					{/each}
				</dl>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title>Conditions <span class="font-mono text-sm font-normal text-muted-foreground">({conditions.length})</span></Card.Title>
				<Card.Description>
					{#if isAgent}
						Each condition is a model × harness preset. Every app runs under every condition with the same brief, stack,
						budget, sampling settings and seed per (app, repeat) cell.
					{:else}
						Each condition is a model × prompt profile; every app runs under every condition.
					{/if}
				</Card.Description>
			</Card.Header>
			<Card.Content class="space-y-3">
				{#if conditions.length > 0}
					<ul class="space-y-1.5">
						{#each conditions as cond (cond.id)}
							{@const chips = conditionChips(cond)}
							{@const overrides = summarizeLLMParams(cond.param_overrides)}
							<li class="flex flex-col gap-2 rounded-md border px-3 py-2 sm:flex-row sm:items-center">
								<div class="min-w-0 flex-1 space-y-1">
									<div class="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
										<span class="font-medium">{cond.model_name ?? `model #${cond.model}`}</span>
										{#if isAgent}
											<span class="text-muted-foreground" aria-hidden="true">×</span>
											<Badge variant="outline">{conditionHarnessLabel(cond)}</Badge>
										{/if}
										{#if cond.bundle_slug}
											<span class="text-muted-foreground" aria-hidden="true">×</span>
											<span class="font-mono text-xs">{cond.bundle_slug}@v{cond.bundle_version}</span>
										{/if}
									</div>
									{#if chips.length > 0}
										<div class="flex flex-wrap gap-1">
											{#each chips as chip (chip)}
												<span class="rounded-full border border-border px-1.5 font-mono text-[11px] text-muted-foreground">{chip}</span>
											{/each}
										</div>
									{/if}
									{#if overrides}
										<p class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
											<Badge variant="outline" class="border-warning/30 bg-warning/10 text-warning-fg">ablation</Badge>
											<span class="font-mono">{overrides}</span>
										</p>
									{/if}
								</div>
								{#if isDraft}
									<Button
										variant="ghost"
										size="icon"
										class="h-7 w-7 self-end text-muted-foreground hover:text-destructive sm:self-center"
										onclick={() => removeCondition(cond)}
										aria-label="Remove condition {conditionLabel(cond)}"
									>
										<Trash2 class="h-3.5 w-3.5" />
									</Button>
								{/if}
							</li>
						{/each}
					</ul>
				{:else}
					<EmptyState
						compact
						icon={GitCompareArrows}
						title="No conditions yet"
						description={isDraft
							? isAgent
								? 'Add at least one model × harness preset below.'
								: 'Add at least one model × profile below.'
							: undefined}
					/>
				{/if}

				{#if isDraft}
					<div class="space-y-4 rounded-md border bg-muted/30 p-3">
						<p class="section-label">Add condition</p>
						<div class="grid gap-4 md:grid-cols-2">
							<div class="min-w-0 space-y-3">
								{#if isAgent}
									<PresetPicker
										kind="harness"
										bind:selectedId={newHarnessPresetId}
										idPrefix="cond-harness"
										label="Harness preset"
									/>
									{#if newHarnessName && harnessInfo(newHarnessName) && !harnessInfo(newHarnessName)?.requires_tool_calling}
										<p class="text-[11px] text-muted-foreground">This harness also drives models without tool calling.</p>
									{/if}
									<div>
										<button
											type="button"
											class="flex cursor-pointer items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
											aria-expanded={showProfile}
											aria-controls="cond-profile-panel"
											onclick={() => (showProfile = !showProfile)}
										>
											<ChevronRight class={cn('size-3.5 transition-transform duration-150', showProfile && 'rotate-90')} aria-hidden="true" />
											Profile LLM defaults (optional)
											{#if showProfile && newProfileId != null}<span class="text-primary">(1 set)</span>{/if}
										</button>
										{#if showProfile}
											<div id="cond-profile-panel" class="mt-2 space-y-1.5">
												<Label class="text-xs" for="cond-profile">Profile</Label>
												<Select.Simple
													id="cond-profile"
													size="sm"
													value={newProfileId != null ? String(newProfileId) : ''}
													items={agentProfileItems}
													placeholder="No profile"
													clearLabel="No profile"
													onValueChange={(v) => (newProfileId = v ? Number(v) : null)}
												/>
												<p class="text-[11px] text-muted-foreground">
													Agent runs use only the profile's LLM defaults (below the sampling preset), not its prompts.
												</p>
											</div>
										{/if}
									</div>
								{:else}
									<ProfilePicker
										bind:selectedId={newProfileId}
										profiles={profilesLoaded ? profiles : undefined}
										appIds={experiment.app_requirement_ids}
										stackSlug={experiment.stack_slug ?? ''}
										idPrefix="cond-profile"
									/>
								{/if}
							</div>
							<div class="min-w-0">
								<ModelPicker
									mode="single"
									bind:selectedId={newModelId}
									{requireTools}
									maxHeight="max-h-64"
									label="Model"
									idPrefix="cond-model"
								/>
							</div>
						</div>

						<div>
							<button
								type="button"
								class="flex cursor-pointer items-center gap-1 rounded text-xs font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
								aria-expanded={showOverrides}
								aria-controls="cond-overrides-panel"
								onclick={() => (showOverrides = !showOverrides)}
							>
								<ChevronRight class={cn('size-3.5 transition-transform duration-150', showOverrides && 'rotate-90')} aria-hidden="true" />
								Per-condition overrides (ablation)
								{#if Object.keys(cleanLLMParams(newOverrides)).length > 0}
									<span class="text-primary">({Object.keys(cleanLLMParams(newOverrides)).length} set)</span>
								{/if}
							</button>
							{#if showOverrides}
								<div id="cond-overrides-panel" class="mt-2 space-y-2">
									<p class="flex items-start gap-1.5 text-[11px] text-warning-fg">
										<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
										Overrides make this condition differ from the others on purpose; the comparability check lists them.
									</p>
									<LLMParamsEditor
										bind:params={newOverrides}
										inherited={overridesInherited}
										mode={isAgent ? 'agent' : 'scaffolding'}
										idPrefix="cond-overrides"
										bind:invalid={overridesInvalid}
										hint=""
									/>
								</div>
							{/if}
						</div>

						{#if addError}
							<ErrorAlert message={addError} />
						{/if}
						<div class="flex flex-col-reverse gap-2 sm:flex-row sm:items-center sm:justify-end">
							{#if addBlocker && (newModelId != null || duplicateCondition)}
								<p class="text-xs text-muted-foreground sm:mr-auto">{addBlocker}</p>
							{/if}
							<Button size="sm" class="self-end" disabled={!!addBlocker || addingCondition} onclick={addCondition}>
								{#if addingCondition}<LoaderCircle class="animate-spin" />{:else}<Plus />{/if}
								Add condition
							</Button>
						</div>
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if isDraft}
			<Card.Root>
				<Card.Header>
					<Card.Title>Preview &amp; launch</Card.Title>
					<Card.Description>Check the run count, the cost bound and what each condition will actually run with.</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3">
					<div class="flex flex-wrap items-center gap-2">
						<Button variant="outline" size="sm" onclick={runPreview} disabled={previewing || conditions.length === 0}>
							{#if previewing}<LoaderCircle class="animate-spin" />{:else}<Calculator />{/if}
							Preview
						</Button>
						<Button
							size="sm"
							onclick={askLaunch}
							disabled={launching ||
								previewing ||
								conditions.length === 0 ||
								experiment.app_requirement_ids.length === 0 ||
								blockingErrors.length > 0}
						>
							{#if launching}<LoaderCircle class="animate-spin" />{:else}<Rocket />{/if}
							Launch
						</Button>
						{#if conditions.length === 0}
							<span class="text-xs text-muted-foreground">Add a condition first.</span>
						{:else if experiment.app_requirement_ids.length === 0}
							<span class="text-xs text-muted-foreground">This experiment has no apps.</span>
						{:else if blockingErrors.length > 0}
							<span class="text-xs text-danger-fg">Fix the blocking problems below, then preview again.</span>
						{/if}
					</div>

					{@render blockingList()}

					{#if previewResult}
						<div class="rounded-md border bg-muted/30 p-3 text-sm tabular-nums">
							<div class="flex flex-wrap gap-x-6 gap-y-1">
								<span><span class="font-medium">{previewResult.total_jobs}</span> {plural(previewResult.total_jobs, 'run')}</span>
								{#if previewResult.pending_jobs != null && previewResult.pending_jobs !== previewResult.total_jobs}
									<span><span class="font-medium">{previewResult.pending_jobs}</span> still to run</span>
								{/if}
								<span><span class="font-medium">{previewResult.conditions}</span> {plural(previewResult.conditions, 'condition')}</span>
								<span><span class="font-medium">{previewResult.apps}</span> {plural(previewResult.apps, 'app')}</span>
								<span><span class="font-medium">{previewResult.repeats}</span>× repeats</span>
								<span>
									{previewResult.cost_estimate_kind === 'budget_upper_bound' ? 'cost cap (upper bound)' : 'est. cost'}
									<span class="font-medium">{previewCost(previewResult)}</span>
								</span>
							</div>
						</div>
						{#if previewResult.comparability}
							{@const comp = previewResult.comparability}
							<div class="space-y-2">
								<p class="section-label">Comparability</p>
								{#if comp.has_problems}
									<div class="rounded-md border px-3 py-2 text-xs {toneClasses.warning}" role="status">
										Some conditions will not run exactly as configured. Fix these before launching, or report them with the results.
									</div>
								{/if}
								{#if comp.differs_on.length}
									<p class="text-xs text-muted-foreground">
										Conditions differ on: <span class="font-mono text-foreground">{comp.differs_on.join(', ')}</span>
									</p>
								{:else}
									<p class="text-xs text-muted-foreground">Conditions differ only in model and harness preset.</p>
								{/if}
								<div class="overflow-x-auto rounded-md border">
									<table class="w-full min-w-[40rem] text-xs">
										<thead class="bg-muted/40 text-left text-muted-foreground">
											<tr>
												<th scope="col" class="px-2 py-1.5 font-medium">Condition</th>
												{#each compColumns as column (column.id)}
													<th scope="col" class={cn('px-2 py-1.5 font-medium', columnDiffers(column) && 'text-warning-fg')}>
														{column.label}{#if columnDiffers(column)}{' '}<span class="font-normal">· differs</span>{/if}
													</th>
												{/each}
												<th scope="col" class="px-2 py-1.5 font-medium">Problems</th>
											</tr>
										</thead>
										<tbody class="divide-y">
											{#each compRows as row (row.condition_id)}
												<tr>
													<td class="px-2 py-1.5 font-mono">{compRowLabel(row)}</td>
													{#each compColumns as column (column.id)}
														<td class={cn('px-2 py-1.5 font-mono', columnDiffers(column) && 'bg-warning/5')}>{column.cell(row)}</td>
													{/each}
													<td class="min-w-[12rem] px-2 py-1.5">
														{#if row.problems.length === 0}
															<span class="text-muted-foreground">—</span>
														{:else}
															<ul class="space-y-0.5">
																{#each row.problems as problem, i (i)}
																	{@const blocking = row.errors?.includes(problem)}
																	<li class={blocking ? 'text-danger-fg' : 'text-warning-fg'}>
																		{#if blocking}<span class="font-medium">Blocks launch:</span>{' '}{/if}{problem}
																	</li>
																{/each}
															</ul>
														{/if}
													</td>
												</tr>
											{/each}
										</tbody>
									</table>
								</div>
							</div>
						{:else if previewResult.per_condition.length > 0}
							<div class="overflow-x-auto rounded-md border">
								<table class="w-full text-xs">
									<thead class="bg-muted/40 text-left text-muted-foreground">
										<tr>
											<th scope="col" class="px-2 py-1.5 font-medium">Condition</th>
											<th scope="col" class="px-2 py-1.5 text-right font-medium">Runs</th>
											<th scope="col" class="px-2 py-1.5 text-right font-medium">Est. cost</th>
										</tr>
									</thead>
									<tbody class="divide-y">
										{#each previewResult.per_condition as row (row.condition_id)}
											<tr>
												<td class="px-2 py-1.5">{conditionById.has(row.condition_id) ? conditionLabel(conditionById.get(row.condition_id)!) : row.label}</td>
												<td class="px-2 py-1.5 text-right tabular-nums">{row.jobs}</td>
												<td class="px-2 py-1.5 text-right tabular-nums">
													{formatUsd(row.estimated_cost)}
												</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{/if}
					{/if}

					{#if launchError}
						<ErrorAlert title="Launch failed" message={launchError} />
					{/if}
				</Card.Content>
			</Card.Root>
		{:else}
			<Card.Root>
				<Card.Header class="flex flex-row items-start justify-between gap-2 space-y-0">
					<div class="space-y-1">
						<Card.Title>Progress</Card.Title>
						<Card.Description>
							One square per run, labelled with its repeat index; open one for its details.
							{#if hasLiveRuns}Refreshes every {POLL_MS / 1000} s while runs are active.{/if}
						</Card.Description>
					</div>
					<div class="flex flex-wrap justify-end gap-2">
						{#if experiment.status !== 'archived' && failedRuns > 0 && !hasLiveRuns}
							<Button
								variant="outline"
								size="sm"
								onclick={askLaunch}
								disabled={launching || previewing}
								title="Create new runs for the failed and cancelled cells, with their original seeds"
							>
								{#if launching || previewing}<LoaderCircle class="animate-spin" />{:else}<Rocket />{/if}
								Re-run failed
							</Button>
						{/if}
						<Button variant="outline" size="sm" onclick={() => refreshStatus()} disabled={statusLoading}>
							{#if statusLoading}<LoaderCircle class="animate-spin" />{:else}<RefreshCw />{/if}
							Refresh
						</Button>
					</div>
				</Card.Header>
				<Card.Content class="space-y-4">
					{@render blockingList()}
					{#if launchError}
						<ErrorAlert title="Launch failed" message={launchError} />
					{/if}
					{#if launchedBatch}
						<p class="text-sm" role="status">
							Launched as batch
							<a class="font-medium text-primary underline-offset-2 hover:underline" href="/sample-generator/batches/{launchedBatch.id}">
								{launchedBatch.name}
							</a>.
						</p>
					{/if}
					{#if statusError}
						<ErrorAlert message={statusError} onretry={() => refreshStatus()} />
					{/if}
					{#if !status}
						{#if statusLoading}
							<LoadingState label="loading progress" class="py-8" />
						{:else if !statusError}
							<EmptyState compact title="No progress yet" description="Refresh to fetch the latest run status." />
						{/if}
					{:else}
						<div class="flex flex-wrap items-center gap-2">
							{#each Object.entries(status.by_status) as [s, count] (s)}
								<StatusBadge status={s} label="{s.replace(/_/g, ' ')}: {count}" />
							{/each}
							<span class="text-xs text-muted-foreground tabular-nums">
								{status.jobs_created} / {status.total_cells} runs · cost so far {formatUsd(status.running_cost)}
							</span>
						</div>
						{#if progressGroups.length === 0}
							<EmptyState compact title="No runs yet" />
						{/if}
						<div class="space-y-3">
							{#each progressGroups as group (group.id)}
								<section class="space-y-1.5" aria-label={group.label}>
									<p class="text-xs font-medium">{group.label}</p>
									<div class="grid gap-1.5 sm:grid-cols-2">
										{#each group.cells as cell (cell.condition_id + ':' + cell.app_id)}
											{@const app = appName.get(cell.app_id) ?? `App #${cell.app_id}`}
											<div class="flex min-w-0 items-center gap-2 rounded-md border px-2.5 py-1.5 text-xs">
												<span class="min-w-0 flex-1 truncate text-muted-foreground" title={app}>{app}</span>
												<div class="flex shrink-0 flex-wrap gap-1">
													{#each [...cell.runs].sort((a, b) => a.repeat_index - b.repeat_index) as run (run.job_id)}
														<a
															href="/applications/{run.job_id}"
															class="rounded border px-1.5 py-0.5 tabular-nums transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 {toneClasses[statusTone(run.status)]}"
															title="{app} · repeat {run.repeat_index} · {run.status}"
															aria-label="{app}, repeat {run.repeat_index}, {run.status}"
														>
															{run.repeat_index}
														</a>
													{/each}
												</div>
											</div>
										{/each}
									</div>
								</section>
							{/each}
						</div>
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={launchConfirmOpen}
	title={!previewResult
		? 'Launch the experiment?'
		: isDraft
			? `Launch ${launchRuns} ${plural(launchRuns, 'run')}?`
			: `Re-run ${launchRuns} ${plural(launchRuns, 'cell')}?`}
	description={previewResult
		? isDraft
			? `${previewResult.conditions} ${plural(previewResult.conditions, 'condition')} × ${previewResult.apps} ${plural(previewResult.apps, 'app')} × ${previewResult.repeats} ${plural(previewResult.repeats, 'repeat')}. ${
					previewResult.cost_estimate_kind === 'budget_upper_bound' ? 'Cost cap (upper bound)' : 'Estimated cost'
				}: ${previewCost(previewResult)} for the whole matrix. The experiment's design is frozen once it launches.`
			: `Re-runs the ${launchRuns} failed or cancelled cell${launchRuns === 1 ? '' : 's'} with the seeds of their (app, repeat) cells. Completed runs are kept.`
		: ''}
	confirmLabel={isDraft ? 'Launch' : 'Re-run'}
	busy={launching}
	onConfirm={doLaunch}
/>

<ConfirmDialog
	bind:open={archiveConfirmOpen}
	title="Archive this experiment?"
	description="It moves out of the active list. Its runs and results are kept."
	confirmLabel="Archive"
	destructive
	busy={archiving}
	onConfirm={doArchive}
/>
