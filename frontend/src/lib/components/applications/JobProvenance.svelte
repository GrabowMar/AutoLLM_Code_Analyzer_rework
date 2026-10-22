<script lang="ts">
	// What one run was configured with (presets, harness options, explicit
	// sampling params, identity keys) and how to reproduce it. Preset values
	// are applied into the job's own fields at creation, so the labels here
	// are provenance; retry/replay/rerun reproduce the configuration itself.
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import {
		getAgentConfig,
		type GenerationJob,
		type HarnessOptionField,
		type NamedPresetRef,
	} from '$lib/api/generation';
	import { harnessOptionChips, summarizeBudget, summarizeLLMParams } from '$lib/components/generator';
	import { batchHref } from '$lib/components/generator/batches';
	import { copyText } from './utils';
	import Copy from '@lucide/svelte/icons/copy';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Repeat from '@lucide/svelte/icons/repeat';
	import Shuffle from '@lucide/svelte/icons/shuffle';
	import CopyPlus from '@lucide/svelte/icons/copy-plus';

	interface Props {
		job: GenerationJob;
		/** Which reproduce action is in flight ('replay' | 'rerun'), if any. */
		busy?: string | null;
		onReplay: () => void;
		onRerun: () => void;
	}

	let { job, busy = null, onReplay, onRerun }: Props = $props();

	type SnapshotPresets = Partial<Record<'harness' | 'sampling' | 'budget', NamedPresetRef | null>>;

	const snapshot = $derived((job.resolved_bundle ?? {}) as Record<string, any>);
	const isAgent = $derived(job.mode === 'agent');
	// Job fields first; the snapshot's own provenance covers older payloads.
	const snapPresets = $derived((snapshot.presets ?? {}) as SnapshotPresets);
	const harnessPreset = $derived(job.harness_preset ?? snapPresets.harness ?? null);
	const samplingPreset = $derived(job.sampling_preset ?? snapPresets.sampling ?? null);
	const budgetPreset = $derived(job.budget_preset ?? snapPresets.budget ?? null);
	const harnessOptions = $derived(job.harness_options ?? snapshot.harness_options ?? {});
	const configFingerprint = $derived(job.config_fingerprint ?? snapshot.config_fingerprint ?? null);
	const runFingerprint = $derived((snapshot.run_fingerprint as string | undefined) ?? null);
	const harnessName = $derived(
		(snapshot.harness?.name as string | undefined) ?? job.agent_config?.harness ?? null
	);
	const harnessVersion = $derived((snapshot.harness?.version as string | undefined) ?? null);
	const llmSummary = $derived(summarizeLLMParams(job.llm_params));
	// Snapshot `agent` is the resolved budget (authoritative); job.agent_config the overrides.
	const budgetSummary = $derived(isAgent ? summarizeBudget(snapshot.agent ?? job.agent_config) : '');
	const live = $derived(job.status === 'pending' || job.status === 'running');
	const canClone = $derived(!live && Object.keys(snapshot).length > 0);

	// Option labels come from the harness schema; plain key=value until it loads.
	let optionSchema = $state<HarnessOptionField[]>([]);
	$effect(() => {
		const name = harnessName;
		if (!isAgent || !name || Object.keys(harnessOptions).length === 0) return;
		let stale = false;
		getAgentConfig()
			.then((config) => {
				if (!stale) optionSchema = config.harnesses.find((h) => h.name === name)?.option_schema ?? [];
			})
			.catch(() => {});
		return () => {
			stale = true;
		};
	});
	const optionChips = $derived(harnessOptionChips(harnessOptions, optionSchema));

	function presetText(preset: NamedPresetRef | null): string {
		return preset ? `${preset.slug} v${preset.version}` : '';
	}
</script>

{#snippet presetValue(preset: NamedPresetRef | null, fallback: string)}
	{#if preset}
		<span class="block truncate" title={preset.name}>{preset.name}</span>
		<span class="block font-mono text-[11px] text-muted-foreground">{presetText(preset)}</span>
	{:else}
		<span class="text-muted-foreground">{fallback}</span>
	{/if}
{/snippet}

{#snippet hashValue(value: string | null, label: string)}
	{#if value}
		<button
			type="button"
			class="inline-flex max-w-full cursor-pointer items-center gap-1 font-mono text-xs hover:text-primary transition-colors"
			onclick={() => copyText(value, `Copied ${label.toLowerCase()}`)}
			title="Copy {label.toLowerCase()}: {value}"
		>
			<span class="truncate">{value}</span><Copy class="h-3 w-3 shrink-0" />
		</button>
	{:else}
		<span class="text-muted-foreground">—</span>
	{/if}
{/snippet}

<Card.Root>
	<Card.Header class="pb-2">
		<Card.Title class="text-sm">Run configuration</Card.Title>
		<Card.Description>
			Presets were applied when the run was created; the job keeps their values, so retries and reruns repeat this
			configuration.
		</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		<dl class="grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-3">
			<div class="min-w-0">
				<dt class="section-label">Task</dt>
				<dd class="truncate">
					{job.template_name ?? (job.task_description ? 'Free-form task (not graded)' : '—')}
				</dd>
			</div>
			<div class="min-w-0">
				<dt class="section-label">Stack</dt>
				<dd class="truncate font-mono text-xs">{job.stack_slug || '—'}</dd>
			</div>
			{#if job.bundle_slug && !job.bundle_slug.startsWith('agent-')}
				<div class="min-w-0">
					<dt class="section-label">Profile</dt>
					<dd class="truncate" title={job.bundle_slug}>{job.bundle_name ?? job.bundle_slug}</dd>
				</div>
			{/if}
			{#if job.batch_id}
				<div class="min-w-0">
					<dt class="section-label">Batch</dt>
					<dd class="truncate">
						<a href={batchHref(job.batch_id)} class="text-primary hover:underline">{job.batch_name || 'Open batch'}</a>
					</dd>
				</div>
			{/if}

			{#if isAgent}
				<div class="min-w-0">
					<dt class="section-label">Harness</dt>
					<dd class="font-mono text-xs">
						{harnessName ?? '—'}{harnessVersion ? `@${harnessVersion}` : ''}
					</dd>
				</div>
				<div class="min-w-0">
					<dt class="section-label">Harness preset</dt>
					<dd>{@render presetValue(harnessPreset, 'none recorded')}</dd>
				</div>
				<div class="min-w-0">
					<dt class="section-label">Harness options</dt>
					<dd class="flex flex-wrap gap-1">
						{#each optionChips as chip (chip)}
							<Badge variant="outline" class="font-mono">{chip}</Badge>
						{:else}
							<span class="text-muted-foreground">harness defaults</span>
						{/each}
					</dd>
				</div>
			{/if}

			<div class="min-w-0">
				<dt class="section-label">Sampling preset</dt>
				<dd>{@render presetValue(samplingPreset, isAgent ? 'none recorded' : 'none')}</dd>
			</div>
			<div class="min-w-0">
				<dt class="section-label">Explicit sampling params</dt>
				<dd class="break-words font-mono text-xs">
					{llmSummary || (isAgent ? 'none — provider defaults' : '—')}
				</dd>
			</div>

			{#if isAgent}
				<div class="min-w-0">
					<dt class="section-label">Budget preset</dt>
					<dd>{@render presetValue(budgetPreset, 'none recorded')}</dd>
				</div>
				<div class="min-w-0">
					<dt class="section-label">Effective budget</dt>
					<dd class="break-words text-xs">{budgetSummary || 'platform defaults'}</dd>
				</div>
			{/if}

			<div class="min-w-0">
				<dt class="section-label">Seed</dt>
				<dd class="font-mono text-xs">{job.experiment_seed ?? snapshot.seed ?? '—'}</dd>
			</div>
		</dl>

		<dl class="grid grid-cols-1 gap-x-6 gap-y-2 border-t border-border pt-3 text-sm sm:grid-cols-3">
			<div class="min-w-0">
				<dt class="section-label" title="Seed-free identity of this configuration; replicates share it">Config fingerprint</dt>
				<dd class="min-w-0">{@render hashValue(configFingerprint, 'Config fingerprint')}</dd>
			</div>
			<div class="min-w-0">
				<dt class="section-label" title="Configuration plus seed">Run fingerprint</dt>
				<dd class="min-w-0">{@render hashValue(runFingerprint, 'Run fingerprint')}</dd>
			</div>
			<div class="min-w-0">
				<dt class="section-label">Prompt hash</dt>
				<dd class="min-w-0">{@render hashValue(job.prompt_hash || null, 'Prompt hash')}</dd>
			</div>
		</dl>

		<div class="space-y-2 border-t border-border pt-3">
			<p class="section-label">Reproduce</p>
			<div class="grid gap-2 sm:grid-cols-3">
				<div class="space-y-1">
					<Button variant="outline" size="sm" class="w-full sm:w-auto" disabled={!canClone || !!busy} onclick={onReplay}>
						{#if busy === 'replay'}<LoaderCircle class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Repeat class="h-3.5 w-3.5" />{/if}
						Replay exactly
					</Button>
					<p class="text-[11px] text-muted-foreground">Same snapshot and seed, as a separate run outside the batch.</p>
				</div>
				<div class="space-y-1">
					<Button variant="outline" size="sm" class="w-full sm:w-auto" disabled={!canClone || !!busy} onclick={onRerun}>
						{#if busy === 'rerun'}<LoaderCircle class="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Shuffle class="h-3.5 w-3.5" />{/if}
						Run another trial
					</Button>
					<p class="text-[11px] text-muted-foreground">
						Same snapshot, new seed{job.batch_id ? '; added to this batch' : ''}.
					</p>
				</div>
				<div class="space-y-1">
					<Button variant="outline" size="sm" class="w-full sm:w-auto" href="/sample-generator?from_job={job.id}">
						<CopyPlus class="h-3.5 w-3.5" />New batch from this run
					</Button>
					<p class="text-[11px] text-muted-foreground">Sample Generator with this task, model, stack and preset versions.</p>
				</div>
			</div>
			{#if live}
				<p class="text-[11px] text-muted-foreground">Replay and rerun are available once this run has finished.</p>
			{:else if !canClone}
				<p class="text-[11px] text-muted-foreground">This run has no frozen snapshot, so it cannot be replayed.</p>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
