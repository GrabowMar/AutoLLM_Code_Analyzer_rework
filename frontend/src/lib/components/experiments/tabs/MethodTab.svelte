<script lang="ts">
	/**
	 * MethodTab — §2 Method, written as a methods section: 2.1 the design as
	 * factors (manipulated, blocking, replication) with Table M1, 2.2 every
	 * condition (Table M2: model, where it runs, variant, overrides, weights
	 * digest, context), 2.3 what is held constant, 2.4 the comparability check
	 * (Table M3) and 2.5 the procedure (run order, seeds, retries, post-run).
	 * Read-only: drafts link to the composer; execution limits of a launched
	 * experiment are edited under Operations.
	 *
	 * Props: `experiment`, `appName(id)`, `policyEffective`.
	 */
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { summarizeBudget, summarizeLLMParams } from '$lib/components/generator';
	import { shortDigest, formatCtx } from '$lib/components/inference';
	import { tableFrame } from '$lib/components/table';
	import { getPreview, type ExperimentConditionV2, type ExperimentDetail, type ExperimentPreviewV2 } from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import Pencil from '@lucide/svelte/icons/pencil';
	import ComparabilityTable from '../ComparabilityTable.svelte';
	import { POLICY_DESCRIPTIONS, conditionName, effectivePolicyLabel, plural, policyLabel } from '../format';
	import type { SchedulingPolicy } from '$lib/api/types/experiments';
	import StudyFigure from '../study/StudyFigure.svelte';
	import StudySection from '../study/StudySection.svelte';
	import { designShape, variantKey } from '../study/findings';

	interface Props {
		experiment: ExperimentDetail;
		appName: (id: number) => string;
		policyEffective?: Record<string, string> | string | null;
	}

	let { experiment, appName, policyEffective = null }: Props = $props();

	const isDraft = $derived(experiment.status === 'draft');
	const isAgent = $derived(experiment.mode === 'agent');
	const shape = $derived(designShape(experiment));

	let preview = $state.raw<ExperimentPreviewV2 | null>(null);
	let previewError = $state('');
	let previewLoading = $state(true);

	async function loadPreview() {
		previewLoading = true;
		previewError = '';
		try {
			preview = await getPreview(experiment.id);
		} catch (e) {
			previewError = formatApiError(e, 'Could not load the comparability check.');
		} finally {
			previewLoading = false;
		}
	}
	onMount(() => void loadPreview());

	const numCtx = $derived(new Map((preview?.endpoints ?? []).flatMap((e) => e.models.map((m) => [m.id, m.num_ctx] as const))));
	const profileName = $derived(new Map((preview?.per_condition ?? []).map((p) => [p.condition_id, p.profile ?? null])));

	const hasOverrides = (c: ExperimentConditionV2) => Object.keys(c.param_overrides ?? {}).length > 0;

	function variantLabel(c: ExperimentConditionV2): string {
		if (c.harness_preset_ref) return `${c.harness_preset_ref.name} v${c.harness_preset_ref.version}`;
		if (c.harness) return c.harness;
		if (c.profile != null) return profileName.get(c.id) ?? `Profile #${c.profile}`;
		return isAgent ? 'Default harness' : 'Default profile';
	}
	const modelLabel = (c: ExperimentConditionV2) => c.model_name ?? `Model #${c.model}`;
	const endpointLabel = (c: ExperimentConditionV2) => c.endpoint?.slug ?? 'openrouter';

	/** Unique values in first-seen order. */
	function levels<T>(items: T[], key: (item: T) => string, label: (item: T) => string): string[] {
		const seen = new Map<string, string>();
		for (const item of items) if (!seen.has(key(item))) seen.set(key(item), label(item));
		return [...seen.values()];
	}

	const factors = $derived.by(() => {
		const crossed = experiment.conditions.filter((c) => !hasOverrides(c));
		const ablations = experiment.conditions.filter(hasOverrides);
		const apps = experiment.app_requirement_ids.map(appName);
		const list: { name: string; role: string; levels: string[]; count: number; note?: string }[] = [
			{
				name: 'Model',
				role: 'manipulated',
				levels: levels(experiment.conditions, (c) => String(c.model), modelLabel),
				count: shape.models,
			},
			{
				name: isAgent ? 'Harness preset' : 'Prompt profile',
				role: 'manipulated',
				levels: levels(crossed.length ? crossed : experiment.conditions, variantKey, variantLabel),
				count: shape.variants,
			},
		];
		if (ablations.length) {
			list.push({
				name: 'Ablation',
				role: 'manipulated',
				levels: ablations.map((c) => `${conditionName(c)} (${summarizeLLMParams(c.param_overrides)})`),
				count: ablations.length,
				note: 'Extra conditions that change sampling parameters only.',
			});
		}
		list.push(
			{ name: 'App spec', role: 'blocking', levels: apps.length ? apps : ['None'], count: apps.length, note: 'Comparisons are paired within each app.' },
			{
				name: 'Repeat',
				role: 'replication',
				levels: [`1 … ${experiment.repeats}`],
				count: experiment.repeats,
				note: experiment.base_seed != null ? 'Repeat r uses the same seed in every condition (common random numbers).' : 'Each run draws its own seed.',
			}
		);
		return list;
	});

	const conditions = $derived(
		[...experiment.conditions].sort(
			(a, b) =>
				Number(b.id === experiment.baseline_condition_id) - Number(a.id === experiment.baseline_condition_id) ||
				endpointLabel(a).localeCompare(endpointLabel(b)) ||
				a.id - b.id
		)
	);

	function presetText(ref: { name: string; version: number } | null | undefined, fallback: string): string {
		return ref ? `${ref.name} v${ref.version}` : fallback;
	}

	const constants = $derived.by(() => {
		const e = experiment;
		const list: { label: string; value: string; mono?: boolean }[] = [
			{ label: 'Generation method', value: isAgent ? 'Agent (a coding harness builds the app in a sandbox)' : 'Template scaffolding (prompted generation into a template)' },
			{ label: 'Stack', value: e.stack_slug || 'Each condition profile’s stack', mono: !!e.stack_slug },
			{ label: 'Sampling preset', value: presetText(e.sampling_preset_ref, isAgent ? 'Provider defaults' : 'Template mode defaults') },
			{ label: 'Sampling overrides', value: summarizeLLMParams(e.llm_defaults) || 'None', mono: !!summarizeLLMParams(e.llm_defaults) },
		];
		if (isAgent) {
			list.push(
				{ label: 'Budget preset', value: presetText(e.budget_preset_ref, 'Platform defaults') },
				{ label: 'Budget overrides', value: summarizeBudget(e.agent_config) || 'None' }
			);
		}
		return list;
	});

	const policy = $derived(experiment.scheduling_policy as SchedulingPolicy);
	const procedure = $derived.by(() => {
		const e = experiment;
		const post = e.post_run ?? {};
		const steps = [
			isAgent ? 'grade with the hidden conformance suite' : post.evaluate ? 'evaluate' : '',
			post.analysis?.enabled ? `static analysis (${post.analysis.tool_slugs?.join(', ') || 'default tools'})` : '',
			post.report?.enabled ? 'write a report' : '',
		].filter(Boolean);
		const list: { label: string; value: string; mono?: boolean }[] = [
			{
				label: 'Run order',
				value: `${isDraft ? policyLabel(policy) : effectivePolicyLabel(policyEffective, policy)} — ${POLICY_DESCRIPTIONS[policy] ?? ''}`,
			},
		];
		if (preview?.schedule) {
			const byEndpoint = Object.entries(preview.schedule.policy_effective_by_endpoint);
			if (byEndpoint.length > 1) list.push({ label: 'Order per endpoint', value: byEndpoint.map(([s, p]) => `${s}: ${policyLabel(p)}`).join(' · ') });
			list.push({ label: 'Order seed', value: preview.schedule.order_seed != null ? String(preview.schedule.order_seed) : '—', mono: true });
			const resident = preview.endpoints.filter((ep) => ep.resident_limit != null);
			if (resident.length) list.push({ label: 'Resident models', value: resident.map((ep) => `${ep.slug}: ${ep.resident_limit}`).join(' · ') });
		}
		list.push(
			{ label: 'Base seed', value: e.base_seed != null ? String(e.base_seed) : 'None — runs draw their own seeds', mono: e.base_seed != null },
			{ label: 'Infrastructure retries', value: `${e.infra_retries} per cell (the same seed; only infrastructure failures retry)` },
			{
				label: 'Model preload',
				value:
					(e.execution_options?.warmup ?? true ? 'On for Ollama endpoints' : 'Off') +
					(e.execution_options?.max_resident_models != null ? ` · at most ${e.execution_options.max_resident_models} resident` : ''),
			},
			{ label: 'Concurrency', value: e.max_concurrency != null ? `At most ${e.max_concurrency} runs at once` : 'Limited by endpoint capacity only' },
			{ label: 'After each run', value: steps.length ? steps.join(' · ') : 'Nothing (runs are not graded)' }
		);
		return list;
	});
</script>

{#snippet definitionList(items: { label: string; value: string; mono?: boolean }[])}
	<dl class="divide-y divide-border/50 overflow-clip rounded-md border border-border bg-card text-sm shadow-[var(--shadow-card)]">
		{#each items as item (item.label)}
			<div class="grid gap-x-4 gap-y-0.5 px-4 py-2 sm:grid-cols-[12rem_minmax(0,1fr)]">
				<dt class="text-xs leading-5 text-muted-foreground">{item.label}</dt>
				<dd class={item.mono ? 'break-words font-mono text-xs leading-5' : 'break-words leading-5'}>{item.value}</dd>
			</div>
		{/each}
	</dl>
{/snippet}

<div class="space-y-10">
	<StudySection
		id="method-design"
		number="2.1"
		title="Design"
		lead="{plural(shape.conditions, 'condition')} — {shape.models > 1 && shape.variants > 1
			? `a full cross of ${shape.models} models and ${shape.variants} ${isAgent ? 'harness presets' : 'profiles'}`
			: `${plural(shape.models, 'model')} × ${plural(shape.variants, isAgent ? 'harness preset' : 'profile')}`}{shape.ablations
			? ` plus ${plural(shape.ablations, 'ablation')}`
			: ''} — each run on {plural(shape.apps, 'app spec')} {plural(shape.repeats, 'time')}: {plural(shape.cells, 'cell')}. Apps are the blocking factor; repeats replicate each cell."
	>
		{#snippet aside()}
			{#if isDraft}<Button size="sm" variant="outline" href="/experiments/new?edit={experiment.id}"><Pencil />Edit design</Button>{/if}
		{/snippet}
		<StudyFigure id="table-m1" kind="table" number="M1" title="Factors" flush>
			<div class="table-frame" {@attach tableFrame}>
				<table class="min-w-[36rem]">
					<thead>
						<tr>
							<th scope="col">Factor</th>
							<th scope="col">Role</th>
							<th scope="col">Levels</th>
							<th scope="col" class="num">n</th>
						</tr>
					</thead>
					<tbody>
						{#each factors as factor (factor.name)}
							<tr>
								<td class="whitespace-nowrap font-medium">{factor.name}</td>
								<td class="whitespace-nowrap font-mono text-xs text-muted-foreground">{factor.role}</td>
								<td class="min-w-60">
									<span class="line-clamp-3 break-words text-sm" title={factor.levels.join(', ')}>{factor.levels.join(', ')}</span>
									{#if factor.note}<span class="cell-sub block text-xs text-muted-foreground">{factor.note}</span>{/if}
								</td>
								<td class="num">{factor.count}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#snippet caption()}
				<strong class="font-medium text-foreground">Table M1.</strong>
				Conditions are the crossed levels of the manipulated factors{shape.ablations ? ' plus the ablations' : ''}. The pre-registered baseline is
				{#if experiment.baseline_condition_id != null}
					<strong class="font-medium text-foreground">{conditionName(experiment.conditions.find((c) => c.id === experiment.baseline_condition_id) ?? { id: experiment.baseline_condition_id, label: '' })}</strong>; every other condition is compared with it, paired by app.
				{:else}
					not set.
				{/if}
			{/snippet}
		</StudyFigure>
	</StudySection>

	<StudySection id="method-conditions" number="2.2" title="Conditions" lead="What each condition is and where it runs. The same weights on two endpoints are two conditions; a changed weights digest after launch is flagged.">
		<StudyFigure id="table-m2" kind="table" number="M2" title="Conditions as registered" flush>
			<div class="table-frame" {@attach tableFrame}>
				<table class="min-w-[52rem]">
					<thead>
						<tr>
							<th scope="col" class="col-sticky">Condition</th>
							<th scope="col">Model</th>
							<th scope="col">Endpoint</th>
							<th scope="col">{isAgent ? 'Harness preset' : 'Profile'}</th>
							<th scope="col">Overrides</th>
							<th scope="col" title="Weights digest (≠ pinned at launch means the model changed)">Digest</th>
							<th scope="col" class="num" title="Effective context (num_ctx)">Context</th>
						</tr>
					</thead>
					<tbody>
						{#each conditions as c (c.id)}
							{@const isBaseline = c.id === experiment.baseline_condition_id}
							{@const overrides = summarizeLLMParams(c.param_overrides)}
							<tr>
								<th
									scope="row"
									class="col-sticky top-auto z-[1] min-w-52 max-w-72 bg-card text-left font-sans text-sm font-normal normal-case tracking-normal text-foreground backdrop-blur-none"
								>
									<span class="flex min-w-0 items-center gap-1.5">
										<span class="line-clamp-2 break-words font-medium" title={conditionName(c)}>{conditionName(c)}</span>
										{#if isBaseline}<span class="shrink-0 rounded-full border border-primary/40 bg-primary/10 px-1.5 text-xs text-primary">baseline</span>{/if}
										{#if overrides}<span class="shrink-0 rounded-full border border-border px-1.5 text-xs text-muted-foreground">ablation</span>{/if}
									</span>
								</th>
								<td class="min-w-40">
									<span class="block truncate">{modelLabel(c)}</span>
									{#if c.served_model_name}<span class="cell-sub block truncate font-mono text-xs text-muted-foreground" title="Served model name">{c.served_model_name}</span>{/if}
								</td>
								<td class="whitespace-nowrap font-mono text-xs">{endpointLabel(c)}</td>
								<td class="whitespace-nowrap text-xs">{variantLabel(c)}</td>
								<td class="font-mono text-xs">{#if overrides}{overrides}{:else}<span class="text-muted-foreground">—</span>{/if}</td>
								<td class="whitespace-nowrap font-mono text-xs">
									{#if c.digest}
										<span title={c.digest}>{shortDigest(c.digest)}</span>{#if c.pinned_digest && c.pinned_digest !== c.digest}<span class="text-warning-fg" title="Pinned at launch: {c.pinned_digest}"> ≠ {shortDigest(c.pinned_digest)}</span>{/if}
									{:else}
										<span class="text-muted-foreground">—</span>
									{/if}
								</td>
								<td class="num text-xs">{formatCtx(numCtx.get(c.model))}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
			{#snippet caption()}
				<strong class="font-medium text-foreground">Table M2.</strong>
				{plural(experiment.conditions.length, 'condition')} on {plural(new Set(experiment.conditions.map(endpointLabel)).size, 'endpoint')}. Digest: the
				first 8 characters of the weights’ sha256 (local endpoints); context: the effective <span class="font-mono">num_ctx</span>.
			{/snippet}
		</StudyFigure>
	</StudySection>

	<StudySection id="method-constants" number="2.3" title="Held constant" lead="Shared by every condition: a condition differs only in what Table M2 lists.">
		{@render definitionList(constants)}
	</StudySection>

	<StudySection
		id="method-comparability"
		number="2.4"
		title="Comparability check"
		lead="What each condition will actually run with after provider limits are applied. Highlighted columns differ between conditions; problems are listed per row."
	>
		{#if previewError}
			<ErrorAlert message={previewError} onretry={loadPreview} />
		{:else if previewLoading && !preview}
			<LoadingState label="checking comparability" class="py-8" />
		{:else if preview?.comparability}
			<StudyFigure id="table-m3" kind="table" number="M3" title="Effective configuration per condition">
				<ComparabilityTable comparability={preview.comparability} experimentStack={experiment.stack_slug} />
			</StudyFigure>
		{:else}
			<p class="text-sm text-muted-foreground">No comparability data for this experiment.</p>
		{/if}
	</StudySection>

	<StudySection id="method-procedure" number="2.5" title="Procedure" lead="How cells are scheduled, seeded and graded. The run order is frozen at launch; limits can be changed under Operations.">
		{@render definitionList(procedure)}
	</StudySection>
</div>
