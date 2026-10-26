<script lang="ts">
	import type { NodeProps } from '@xyflow/svelte';
	import Search from '@lucide/svelte/icons/search';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import NodeShell from './NodeShell.svelte';
	import type { WorkflowNodeData } from '$lib/utils/pipeline-flow';
	import {
		AI_REVIEW_SLUG,
		THRESHOLD_KEYS,
		hasLegacyKeys,
		isUuid,
		profileRef,
		thresholdsOf,
		toolRestriction
	} from '../analyze-step';

	type Props = NodeProps & { data: WorkflowNodeData };
	let { data, selected }: Props = $props();

	const MAX_TOOL_CHIPS = 6;

	const cfg = $derived((data.config ?? {}) as Record<string, unknown>);
	const ref = $derived(profileRef(cfg));
	const tools = $derived(toolRestriction(cfg));
	const thresholds = $derived(thresholdsOf(cfg));
	const gates = $derived(
		THRESHOLD_KEYS.filter((k) => thresholds[k] !== undefined).map((k) => `${k} ≤ ${thresholds[k]}`)
	);
	const legacy = $derived(hasLegacyKeys(cfg));
	const job = $derived(
		typeof cfg.generation_job_id === 'string' && cfg.generation_job_id ? cfg.generation_job_id : ''
	);
</script>

<NodeShell
	name={data.name}
	runStatus={data.runStatus}
	accent="blue"
	icon={Search}
	kindLabel="Analyze"
	selected={selected ?? false}
>
	<div class="flex items-start gap-2">
		<span class="w-12 shrink-0 pt-0.5 text-[11px] uppercase tracking-wider text-muted-foreground"
			>Profile</span
		>
		{#if ref}
			<span class="min-w-0 truncate font-mono text-[11px]" title={ref}>
				{isUuid(ref) ? `pinned ${ref.slice(0, 8)}` : ref}
			</span>
		{:else}
			<span class="text-[11px] text-muted-foreground">research default</span>
		{/if}
	</div>
	<div class="flex items-start gap-2">
		<span class="w-12 shrink-0 pt-0.5 text-[11px] uppercase tracking-wider text-muted-foreground"
			>Tools</span
		>
		{#if tools === null || (tools.length === 0 && ref)}
			<span class="text-[11px] {ref ? 'text-muted-foreground' : 'text-danger-fg'}"
				>{ref ? "profile's tools" : 'none selected'}</span
			>
		{:else if tools.length === 0}
			<span class="text-[11px] text-danger-fg">none selected</span>
		{:else}
			<div class="flex min-w-0 flex-wrap gap-1">
				{#each tools.slice(0, MAX_TOOL_CHIPS) as slug (slug)}
					<span
						class="rounded border border-info/40 bg-info/10 px-1.5 py-0.5 text-[11px] text-info-fg"
					>
						{slug === AI_REVIEW_SLUG ? 'AI review' : slug}
					</span>
				{/each}
				{#if tools.length > MAX_TOOL_CHIPS}
					<span class="px-1 py-0.5 text-[11px] text-muted-foreground"
						>+{tools.length - MAX_TOOL_CHIPS}</span
					>
				{/if}
			</div>
		{/if}
	</div>
	{#if gates.length > 0 || cfg.fail_on_partial}
		<div class="flex items-start gap-2">
			<span class="w-12 shrink-0 pt-0.5 text-[11px] uppercase tracking-wider text-muted-foreground"
				>Gates</span
			>
			<span class="min-w-0 text-[11px]">
				{gates.join(' · ')}{#if cfg.fail_on_partial}{gates.length ? ' · ' : ''}fail if partial{/if}
			</span>
		</div>
	{/if}
	{#if job}
		<div class="mt-1 border-t border-border pt-1">
			<span class="block truncate font-mono text-[11px] text-muted-foreground" title={job}
				>{job}</span
			>
		</div>
	{/if}
	{#if legacy}
		<div
			class="mt-1 flex items-center gap-1 border-t border-border pt-1 text-[11px] text-warning-fg"
		>
			<TriangleAlert class="size-3 shrink-0" aria-hidden="true" />Old analyzer settings — open to
			update
		</div>
	{/if}
</NodeShell>
