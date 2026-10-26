<script lang="ts">
	/**
	 * DesignTab — what the experiment is: the shared design, the conditions
	 * grouped by endpoint (served name, digest, context), the comparability
	 * check and the execution plan. Drafts link to the composer; launched
	 * experiments can change priority, concurrency, retries, preloading and
	 * raise the repeat count here.
	 *
	 * Props: `experiment`, `appName(id)`, `policyEffective`, `onupdated(detail)`.
	 */
	import { onMount, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Switch } from '$lib/components/ui/switch';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import { summarizeLLMParams } from '$lib/components/generator';
	import { shortDigest, formatCtx } from '$lib/components/inference';
	import {
		getPreview,
		updateExperiment,
		type ExperimentConditionV2,
		type ExperimentDetail,
		type ExperimentPreviewV2,
		type ExperimentUpdateV2,
	} from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import Pencil from '@lucide/svelte/icons/pencil';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import ComparabilityTable from '../ComparabilityTable.svelte';
	import ExperimentDesignSummary from '../ExperimentDesignSummary.svelte';
	import { MAX_REPEATS } from '../composer';
	import { conditionName, isTerminal, policyLabel, priorityBucket } from '../format';

	interface Props {
		experiment: ExperimentDetail;
		appName: (id: number) => string;
		policyEffective?: Record<string, string> | string | null;
		onupdated: (detail: ExperimentDetail) => void;
	}

	let { experiment, appName, policyEffective = null, onupdated }: Props = $props();

	const isDraft = $derived(experiment.status === 'draft');

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

	const numCtx = $derived(
		new Map((preview?.endpoints ?? []).flatMap((e) => e.models.map((m) => [m.id, m.num_ctx] as const)))
	);

	const groups = $derived.by(() => {
		const map = new Map<string, ExperimentConditionV2[]>();
		for (const c of experiment.conditions) {
			const key = c.endpoint?.slug ?? 'openrouter';
			const list = map.get(key) ?? [];
			list.push(c);
			map.set(key, list);
		}
		return [...map.entries()].sort(([a], [b]) => (a === 'openrouter' ? 1 : b === 'openrouter' ? -1 : a.localeCompare(b)));
	});

	// ── launched: inline execution edits ────────────────────────────
	let priority = $state('0');
	let maxConcurrency = $state<number | null>(null);
	let infraRetries = $state<number | null>(null);
	let repeats = $state<number | null>(null);
	let warmup = $state(true);

	/** The values last seeded into (or saved from) the form. */
	let seeded: string | null = null;
	const formKey = () => JSON.stringify([priority, maxConcurrency, infraRetries, repeats, warmup]);

	// (Re)seed the form from the saved experiment: on first render, after a
	// save, and on silent detail reloads (state events, lifecycle actions) —
	// but only while the form is unedited, so a reload never drops pending edits.
	$effect(() => {
		const e = experiment;
		void e.updated_at;
		untrack(() => {
			if (seeded !== null && formKey() !== seeded) return;
			priority = String(priorityBucket(e.priority));
			maxConcurrency = e.max_concurrency;
			infraRetries = e.infra_retries;
			repeats = e.repeats;
			warmup = e.execution_options?.warmup ?? true;
			seeded = formKey();
		});
	});
	let invalid = $state({ conc: false, retries: false, repeats: false });
	let saving = $state(false);

	const patch = $derived.by((): ExperimentUpdateV2 => {
		const out: ExperimentUpdateV2 = {};
		// Only a changed level is sent: a staff-set value (e.g. 3) shows as "High"
		// and must not be rewritten to 5 when another field is saved.
		if (Number(priority) !== priorityBucket(experiment.priority)) out.priority = Number(priority);
		if (maxConcurrency !== experiment.max_concurrency) out.max_concurrency = maxConcurrency;
		if (infraRetries != null && infraRetries !== experiment.infra_retries) out.infra_retries = infraRetries;
		if (repeats != null && repeats !== experiment.repeats) out.repeats = repeats;
		if (warmup !== (experiment.execution_options?.warmup ?? true)) {
			out.execution_options = { ...experiment.execution_options, warmup };
		}
		return out;
	});
	const dirty = $derived(Object.keys(patch).length > 0);
	const anyInvalid = $derived(invalid.conc || invalid.retries || invalid.repeats);

	async function save() {
		saving = true;
		try {
			const detail = await updateExperiment(experiment.id, patch);
			seeded = formKey();
			toast.success(patch.repeats ? 'Saved. New repeats run at the next top-up.' : 'Saved');
			onupdated(detail);
		} catch (e) {
			toast.error(formatApiError(e, 'Could not save.'));
		} finally {
			saving = false;
		}
	}

	const PRIORITIES = [
		{ value: '-5', label: 'Low' },
		{ value: '0', label: 'Normal' },
		{ value: '5', label: 'High', description: 'Staff only' },
	];
</script>

<div class="space-y-6">
	<Card.Root>
		<Card.Header class="flex-row items-start justify-between gap-2">
			<div class="space-y-1">
				<Card.Title>Design</Card.Title>
				<Card.Description>Shared by every condition; a condition only varies what its row below says.</Card.Description>
			</div>
			{#if isDraft}
				<Button size="sm" href="/experiments/new?edit={experiment.id}"><Pencil />Edit</Button>
			{/if}
		</Card.Header>
		<Card.Content>
			<ExperimentDesignSummary {experiment} {appName} {policyEffective} />
			{#if experiment.hypothesis}
				<p class="mt-4 border-t border-border/60 pt-3 text-sm italic text-muted-foreground">“{experiment.hypothesis}”</p>
			{/if}
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Conditions <span class="font-mono text-sm font-normal text-muted-foreground">({experiment.conditions.length})</span></Card.Title>
			<Card.Description>Grouped by endpoint. The same weights on two endpoints are two conditions.</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			{#each groups as [key, conditions] (key)}
				<div class="space-y-1.5">
					<p class="section-label">{key === 'openrouter' ? 'OpenRouter' : key} · {conditions.length}</p>
					<ul class="divide-y divide-border/60 rounded-md border border-border">
						{#each conditions as c (c.id)}
							{@const overrides = summarizeLLMParams(c.param_overrides)}
							<li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
								<span class="min-w-0 font-medium">{conditionName(c)}</span>
								{#if c.id === experiment.baseline_condition_id}
									<span class="rounded-full border border-primary/40 bg-primary/10 px-1.5 text-[11px] text-success-fg">baseline</span>
								{/if}
								{#if overrides}
									<Badge variant="outline" class="border-warning/30 bg-warning/10 text-warning-fg">ablation</Badge>
									<span class="font-mono text-[11px] text-muted-foreground">{overrides}</span>
								{/if}
								<span class="ml-auto flex flex-wrap gap-x-3 font-mono text-[11px] text-muted-foreground">
									{#if c.served_model_name}<span title="Served model name">{c.served_model_name}</span>{/if}
									{#if c.digest}
										<span title="Weights digest{c.pinned_digest && c.pinned_digest !== c.digest ? ` (pinned ${shortDigest(c.pinned_digest)} at launch)` : ''}">
											{shortDigest(c.digest)}{#if c.pinned_digest && c.pinned_digest !== c.digest}<span class="text-warning-fg"> ≠ {shortDigest(c.pinned_digest)}</span>{/if}
										</span>
									{/if}
									{#if numCtx.get(c.model) != null}<span title="Effective context (num_ctx)">ctx {formatCtx(numCtx.get(c.model))}</span>{/if}
									{#if c.bundle_slug}<span>{c.bundle_slug}@v{c.bundle_version}</span>{/if}
								</span>
							</li>
						{/each}
					</ul>
				</div>
			{/each}
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Comparability</Card.Title>
			<Card.Description>What each condition actually runs with; highlighted columns differ between conditions.</Card.Description>
		</Card.Header>
		<Card.Content>
			{#if previewError}
				<ErrorAlert message={previewError} onretry={loadPreview} />
			{:else if previewLoading && !preview}
				<LoadingState label="checking comparability" class="py-8" />
			{:else if preview?.comparability}
				<ComparabilityTable comparability={preview.comparability} experimentStack={experiment.stack_slug} />
			{:else}
				<p class="text-xs text-muted-foreground">No comparability data for this experiment.</p>
			{/if}
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Execution</Card.Title>
			<Card.Description>
				{#if isDraft}
					Set in the composer. The run order is frozen at launch.
				{:else}
					The run order is frozen. Priority, limits and preloading can change while it runs; repeats can only grow.
				{/if}
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			{#if preview?.schedule}
				<dl class="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-3">
					<div>
						<dt class="section-label">Order per endpoint</dt>
						<dd class="text-xs">
							{Object.entries(preview.schedule.policy_effective_by_endpoint).map(([s, p]) => `${s}: ${policyLabel(p)}`).join(' · ') ||
								policyLabel(experiment.scheduling_policy)}
						</dd>
					</div>
					<div>
						<dt class="section-label">Order seed</dt>
						<dd class="font-mono text-xs">{preview.schedule.order_seed ?? '—'}</dd>
					</div>
					<div>
						<dt class="section-label">Resident models (K)</dt>
						<dd class="text-xs">
							{preview.endpoints.filter((e) => e.resident_limit != null).map((e) => `${e.slug}: ${e.resident_limit}`).join(' · ') || '—'}
						</dd>
					</div>
				</dl>
			{/if}
			{#if !isDraft && !isTerminal(experiment.status)}
				<div class="grid gap-3 border-t border-border/60 pt-4 sm:grid-cols-2 lg:grid-cols-4">
					<div class="space-y-1.5">
						<Label class="text-xs" for="design-priority">Priority</Label>
						<Select.Simple id="design-priority" size="sm" bind:value={priority} items={PRIORITIES} />
					</div>
					<NumberField id="design-conc" label="Max concurrent runs" bind:value={maxConcurrency} min={1} max={64} placeholder="Unlimited" bind:invalid={invalid.conc} />
					<NumberField id="design-retries" label="Infra retries" bind:value={infraRetries} min={0} max={5} required bind:invalid={invalid.retries} />
					<NumberField
						id="design-repeats"
						label="Repeats per cell"
						bind:value={repeats}
						min={experiment.repeats}
						max={MAX_REPEATS}
						required
						bind:invalid={invalid.repeats}
						hint="Increase only; new repeats run at the next top-up."
					/>
				</div>
				<label class="flex items-center gap-2 text-xs">
					<Switch size="sm" checked={warmup} label="Preload Ollama models" onCheckedChange={(v) => (warmup = v)} />
					Preload Ollama models before their runs
				</label>
				<div class="flex justify-end">
					<Button size="sm" disabled={!dirty || anyInvalid || saving} onclick={save}>
						{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Save />{/if}Save changes
					</Button>
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</div>
