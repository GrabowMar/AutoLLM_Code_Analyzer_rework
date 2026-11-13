<script lang="ts">
	/**
	 * MeasurementStep — what is measured: the evaluation of every sample (part of the design and of the
	 * pre-registration), the analysis plan of a confirmatory experiment (an exploratory one gets a note
	 * with the switch), and the post-run analysis + report. The post-run is no longer a separate
	 * "Attach" click: it is attached when the experiment launches (`attachedPostRuns` lists the ones a
	 * resumed draft already has, so nothing is attached twice).
	 *
	 * Props: `builder`, `locked`, `postRun` (bindable), `attachedPostRuns`.
	 */
	import type { PostRunOut } from '$lib/api/v3/types';
	import { postRunKey } from '../launch';
	import { Button } from '$lib/components/ui/button';
	import PostRunEditor, { type AnalysisPostRunConfig } from '../../automation/PostRunEditor.svelte';
	import type { BuilderState } from '../builder.svelte';
	import { defaultAnalysisPlan, setKind } from '../design';
	import AnalysisPlanSection from '../AnalysisPlanSection.svelte';
	import BuilderGroup from '../BuilderGroup.svelte';
	import EvaluationSection from '../EvaluationSection.svelte';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';

	interface Props {
		builder: BuilderState;
		locked: boolean;
		postRun: AnalysisPostRunConfig;
		attachedPostRuns: PostRunOut[];
	}

	let { builder: b, locked, postRun = $bindable(), attachedPostRuns }: Props = $props();

	const design = $derived(b.design);
	const attached = $derived(attachedPostRuns.filter((p) => p.kind === 'analysis' && p.trigger === 'completed'));
	const same = $derived(attached.some((p) => postRunKey(p.config) === postRunKey(postRun)));
	const wanted = $derived(postRun.tool_slugs.length > 0 || postRun.report);
</script>

<BuilderGroup title="Evaluation" description="Measured for every sample. Part of the design (and of the pre-registration), never of the budget.">
	<EvaluationSection
		value={design.evaluation}
		mode={design.mode}
		disabled={locked}
		machineMinutes={b.plan?.estimate?.time?.eval_machine_minutes ?? null}
		onchange={(evaluation) => b.setDesign({ ...design, evaluation })}
	/>
</BuilderGroup>

{#if design.kind === 'confirmatory'}
	<BuilderGroup title="Analysis plan" description="Pre-registered: normalized and hashed at launch (prereg_hash). Deviations are reported.">
		<AnalysisPlanSection
			value={design.analysis_plan ?? defaultAnalysisPlan(design.mode)}
			mode={design.mode}
			repeats={design.repeats}
			primaryFeatures={design.slot_primary_features}
			disabled={locked}
			onchange={(plan) => b.setDesign({ ...design, analysis_plan: plan })}
			onfeatures={(refs) => b.setDesign({ ...design, slot_primary_features: refs })}
		/>
	</BuilderGroup>
{:else}
	<div class="ms-note">
		<ShieldCheck class="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
		<div class="min-w-0 flex-1 text-xs">
			<p class="font-medium text-foreground">No pre-registered analysis</p>
			<p class="text-muted-foreground">This is an exploratory experiment: the report compares conditions and labels every test exploratory.</p>
		</div>
		<Button variant="outline" size="sm" disabled={locked} onclick={() => b.setDesign(setKind(design, 'confirmatory'))}>Make it confirmatory</Button>
	</div>
{/if}

<BuilderGroup title="After the runs" description="Runs when the experiment completes or is stopped. Schedules are set on the experiment’s Operations page after launch.">
	<PostRunEditor bind:value={postRun} mode={design.mode} disabled={locked} />
	{#if same}
		<p class="flex items-center gap-1.5 text-xs text-success-fg">
			<CircleCheck class="size-3.5" aria-hidden="true" />Already attached to this draft with these settings.
		</p>
	{:else if !wanted}
		<p class="text-[11px] text-muted-foreground">Nothing runs after the experiment: no analyzers and no report.</p>
	{:else if attached.length > 0}
		<p class="text-[11px] text-muted-foreground">This draft already has a post-run with other settings; launching adds this one as well.</p>
	{:else}
		<p class="text-[11px] text-muted-foreground">Attached automatically when you launch.</p>
	{/if}
</BuilderGroup>

<style>
	.ms-note {
		display: flex;
		flex-wrap: wrap;
		align-items: flex-start;
		gap: 0.75rem;
		padding: 0.75rem 0.875rem;
		border: 1px dashed var(--border);
		border-radius: var(--radius);
	}
</style>
