<script lang="ts">
	/**
	 * ExperimentDesignSummary — the experiment-wide design as a read-only
	 * definition list: method, stack, apps, repeats, seed, presets and
	 * overrides, execution plan and post-run steps. Extracted from the old
	 * experiment page.
	 *
	 * Props:
	 * - `experiment`: the detail.
	 * - `appName(id)`: app spec display name.
	 * - `policyEffective`: per-endpoint effective policy once launched.
	 */
	import { cn } from '$lib/utils';
	import { summarizeBudget, summarizeLLMParams } from '$lib/components/generator';
	import type { ExperimentDetail } from '$lib/api/types/experiments';
	import { effectivePolicyLabel, policyLabel } from './format';

	interface Props {
		experiment: ExperimentDetail;
		appName: (id: number) => string;
		policyEffective?: Record<string, string> | string | null;
	}

	let { experiment, appName, policyEffective = null }: Props = $props();

	const isAgent = $derived(experiment.mode === 'agent');

	function presetText(ref: { name: string; version: number } | null | undefined, fallback: string): string {
		return ref ? `${ref.name} v${ref.version}` : fallback;
	}

	const priorityText = (p: number) => (p > 0 ? `High (${p})` : p < 0 ? `Low (${p})` : 'Normal');

	const items = $derived.by(() => {
		const e = experiment;
		const list: { label: string; value: string; mono?: boolean; wide?: boolean }[] = [
			{ label: 'Method', value: isAgent ? 'Agent' : 'Template scaffolding' },
			{ label: 'Stack', value: e.stack_slug || 'each condition profile’s stack', mono: !!e.stack_slug },
			{ label: 'Repeats', value: `${e.repeats}× per cell` },
			{
				label: 'Base seed',
				value: e.base_seed != null ? String(e.base_seed) : 'random per run (no common random numbers)',
				mono: e.base_seed != null,
			},
			{
				label: 'Apps',
				value: e.app_requirement_ids.length ? e.app_requirement_ids.map(appName).join(', ') : 'none',
				wide: true,
			},
			{
				label: 'Sampling preset',
				value: presetText(e.sampling_preset_ref, isAgent ? 'Provider defaults (platform default)' : 'none (template mode defaults)'),
			},
			{
				label: 'Experiment overrides',
				value: summarizeLLMParams(e.llm_defaults) || 'none',
				mono: !!summarizeLLMParams(e.llm_defaults),
			},
		];
		if (isAgent) {
			list.push(
				{ label: 'Budget preset', value: presetText(e.budget_preset_ref, 'Platform defaults') },
				{ label: 'Budget overrides', value: summarizeBudget(e.agent_config) || 'none' }
			);
		}
		list.push(
			{
				label: 'Scheduling',
				value: e.status === 'draft' ? policyLabel(e.scheduling_policy) : effectivePolicyLabel(policyEffective, e.scheduling_policy),
			},
			{ label: 'Priority', value: priorityText(e.priority) },
			{ label: 'Max concurrent runs', value: e.max_concurrency != null ? String(e.max_concurrency) : 'Unlimited' },
			{ label: 'Infra retries', value: String(e.infra_retries) },
			{
				label: 'Model preload',
				value:
					(e.execution_options?.warmup ?? true ? 'On (Ollama endpoints)' : 'Off') +
					(e.execution_options?.max_resident_models != null ? ` · max ${e.execution_options.max_resident_models} resident` : ''),
			}
		);
		const post = e.post_run ?? {};
		const steps = [
			!isAgent && post.evaluate ? 'evaluate' : '',
			post.analysis?.enabled
				? `analysis (${[post.analysis.profile ? `profile ${post.analysis.profile}` : 'default profile', post.analysis.tool_slugs?.length ? post.analysis.tool_slugs.join(', ') : ''].filter(Boolean).join(': ')})`
				: '',
			post.report?.enabled ? 'report' : '',
		].filter(Boolean);
		list.push({ label: 'After the run', value: steps.length ? steps.join(' · ') : 'nothing' });
		if (e.cloned_from) list.push({ label: 'Cloned from', value: e.cloned_from.name });
		return list;
	});
</script>

<dl class="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
	{#each items as item (item.label)}
		<div class={cn('min-w-0', item.wide && 'sm:col-span-2 lg:col-span-3')}>
			<dt class="section-label">{item.label}</dt>
			<dd class={cn('break-words', item.mono && 'font-mono text-xs')}>{item.value}</dd>
		</div>
	{/each}
</dl>
