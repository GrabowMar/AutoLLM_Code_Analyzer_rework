<script lang="ts">
	/**
	 * ContentEditor — the structured editor (or viewer) for one library kind, with a "Raw JSON" switch for
	 * editable kinds. The Library detail and create pages use it; other screens import the kind editors
	 * directly.
	 *
	 * Props: `kind`, `value` (content), `onchange(content, errors)` (`errors` non-empty = cannot be saved),
	 * `readonly`, `base` (content of the version being edited, for diffs and Budget preset overrides),
	 * `slug`/`version` (viewers that fetch, e.g. the starter tree), `tier` (Budget presets), `derived` and
	 * `issues` (tasks), `warnOnFingerprintChange` (Budget presets used by studies).
	 */
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import type {
		DetectorContent,
		EvalSuiteContent,
		GuidanceContent,
		HarnessPresetContent,
		Issue,
		LibraryContentMap,
		LibraryKind,
		PolicyTier,
		PromptSetContent,
		PromptTemplateContent,
		ResourcePolicyContent,
		SamplingContent,
		StackProfileContent,
		StarterContent,
		StyleContent,
		TaskDerived,
		TaskSpecContent,
	} from '$lib/api/v3/types';
	import { POLICY_TIERS } from '$lib/api/v3/types';
	import DetectorEditor from './DetectorEditor.svelte';
	import EvalSuiteViewer from './EvalSuiteViewer.svelte';
	import GuidanceEditor from './GuidanceEditor.svelte';
	import HarnessPresetEditor from './HarnessPresetEditor.svelte';
	import JsonContentEditor from './JsonContentEditor.svelte';
	import PromptSetEditor from './PromptSetEditor.svelte';
	import PromptTemplateEditor from './PromptTemplateEditor.svelte';
	import ResourcePolicyEditor from './ResourcePolicyEditor.svelte';
	import SamplingPresetEditor from './SamplingPresetEditor.svelte';
	import StackProfileEditor from './StackProfileEditor.svelte';
	import StarterViewer from './StarterViewer.svelte';
	import StyleEditor from './StyleEditor.svelte';
	import TaskEditor from './TaskEditor.svelte';
	import { KIND_INFO } from './kinds';
	import { libraryStore } from './library.svelte';
	import { baseTier } from './policy';

	interface Props {
		kind: LibraryKind;
		value: LibraryContentMap[LibraryKind];
		onchange: (content: LibraryContentMap[LibraryKind], errors: Record<string, string>) => void;
		readonly?: boolean;
		base?: LibraryContentMap[LibraryKind] | null;
		slug?: string;
		version?: number;
		tier?: string | null;
		derived?: TaskDerived | null;
		issues?: Issue[];
		warnOnFingerprintChange?: boolean;
		/** Detector prefill (WP-16 "Create detector from selection"). */
		prefill?: Partial<DetectorContent> | null;
	}

	let {
		kind,
		value,
		onchange,
		readonly = false,
		base = null,
		slug = '',
		version = 0,
		tier = null,
		derived: taskDerived = null,
		issues = [],
		warnOnFingerprintChange = false,
		prefill = null,
	}: Props = $props();

	const viewerOnly = $derived(KIND_INFO[kind].editing === 'readonly');
	let mode = $state<'form' | 'json'>('form');
	let jsonError = $state<string | null>(null);

	const emit = (content: unknown, errors: Record<string, string> = {}) =>
		onchange(content as LibraryContentMap[LibraryKind], jsonError && mode === 'json' ? { json: jsonError, ...errors } : errors);

	// Budget presets: the knob catalogue and the base the overrides are counted against.
	$effect(() => {
		if (kind === 'resource_policy') void libraryStore.ensureKnobs();
	});
	const knobs = $derived(libraryStore.knobs);
	const policyBase = $derived.by((): ResourcePolicyContent | null => {
		if (kind !== 'resource_policy' || !knobs) return null;
		if (base) return base as ResourcePolicyContent;
		const t = baseTier(tier, value as ResourcePolicyContent, knobs.tiers, knobs.knobs);
		return POLICY_TIERS.includes(t as PolicyTier) ? knobs.tiers[t] : null;
	});
</script>

<div class="space-y-3">
	{#if !viewerOnly}
		<div class="flex justify-end">
			<Segmented
				aria-label="Editor"
				size="sm"
				value={mode}
				items={[
					{ value: 'form', label: 'Form' },
					{ value: 'json', label: 'Raw JSON' },
				]}
				onValueChange={(v) => (mode = v === 'json' ? 'json' : 'form')}
			/>
		</div>
	{/if}

	{#if mode === 'json' && !viewerOnly}
		<JsonContentEditor
			{value}
			{readonly}
			oninvalid={(m) => {
				jsonError = m;
				if (m) onchange(value, { json: m });
			}}
			onchange={(c) => emit(c)}
		/>
	{:else if kind === 'task'}
		<TaskEditor value={value as TaskSpecContent} {readonly} derived={taskDerived} {issues} onchange={(c, e) => emit(c, e)} />
	{:else if kind === 'stack'}
		<StackProfileEditor value={value as StackProfileContent} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'starter'}
		<StarterViewer {slug} {version} value={value as StarterContent} />
	{:else if kind === 'style'}
		<StyleEditor value={value as StyleContent} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'guidance'}
		<GuidanceEditor value={value as GuidanceContent} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'prompt'}
		<PromptTemplateEditor value={value as PromptTemplateContent} base={base as PromptTemplateContent | null} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'prompt_set'}
		<PromptSetEditor value={value as PromptSetContent} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'harness_preset'}
		<HarnessPresetEditor value={value as HarnessPresetContent} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'sampling'}
		<SamplingPresetEditor value={value as SamplingContent} {readonly} onchange={(c) => emit(c)} />
	{:else if kind === 'resource_policy'}
		{#if libraryStore.knobsError}
			<ErrorAlert message={libraryStore.knobsError} />
		{:else if !knobs}
			<LoadingState label="loading the knob catalogue" />
		{:else}
			<ResourcePolicyEditor
				value={value as ResourcePolicyContent}
				base={policyBase}
				knobMeta={knobs.knobs}
				{readonly}
				{warnOnFingerprintChange}
				onchange={(c) => emit(c)}
			/>
		{/if}
	{:else if kind === 'eval_suite'}
		<EvalSuiteViewer value={value as EvalSuiteContent} />
	{:else if kind === 'detector'}
		<DetectorEditor value={value as DetectorContent} {readonly} {prefill} onchange={(c) => emit(c)} />
	{/if}
</div>
