<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { RadioGroup } from 'bits-ui';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Label } from '$lib/components/ui/label';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { ModelPicker, loadModelCatalog, findModel } from '$lib/components/generator';
	import { REPORT_TYPE_INFO } from '$lib/components/insights/reports/report-types';
	import { shortHash } from '$lib/components/insights/format';
	import { cn } from '$lib/utils';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { listExperiments, type Experiment } from '$lib/api/experiments';
	import { getAppTemplates, type AppRequirementTemplate } from '$lib/api/generation';
	import { listAnalysisTools } from '$lib/api/analysis/catalog';
	import type { AnalysisTool } from '$lib/api/analysis/types';
	import type { LLMModelSummary } from '$lib/api/models';
	import { getRankings } from '$lib/api/rankings';
	import {
		REPORT_TYPES,
		generateReport,
		type ReportConfig,
		type ReportType
	} from '$lib/api/reports';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Check from '@lucide/svelte/icons/check';

	const params = page.url.searchParams;
	const initialType = params.get('type') ?? '';

	let reportType = $state<ReportType>(
		(REPORT_TYPES as readonly string[]).includes(initialType)
			? (initialType as ReportType)
			: 'experiment_report'
	);
	let title = $state('');
	let description = $state('');
	let expires = $state('30');

	// Scope
	let experimentId = $state(params.get('experiment_id') ?? '');
	let modelPk = $state<number | null>(null);
	let pendingModelId = params.get('model_id') ?? '';
	let templateSlug = $state(params.get('template_slug') ?? '');
	let filterModelPks = $state<Set<number>>(new Set());
	let bundleKey = $state(params.get('bundle_key') ?? '');
	let promptHash = $state(params.get('prompt_hash') ?? '');
	let toolName = $state(params.get('tool_name') ?? '');
	let filterModelPk = $state<number | null>(null);
	let days = $state<number | null>(Number(params.get('days')) || 30);

	// Analysis settings (measured report types)
	let planHash = $state(params.get('plan_hash') ?? '');
	let excludeTruncated = $state(true);
	let excludeInvalid = $state(true);
	let excludeEmpty = $state(true);

	let experiments = $state<Experiment[]>([]);
	let templates = $state<AppRequirementTemplate[]>([]);
	let tools = $state<AnalysisTool[]>([]);
	let models = $state<LLMModelSummary[]>([]);
	let plans = $state<Record<string, number>>({});
	let plansLoading = $state(false);

	let submitting = $state(false);
	let error = $state<string | null>(null);
	let fieldErrors = $state<Record<string, string>>({});

	const info = $derived(REPORT_TYPE_INFO[reportType]);

	onMount(() => {
		listExperiments()
			.then((list) => (experiments = list))
			.catch(() => (experiments = []));
		getAppTemplates()
			.then((list) => (templates = list))
			.catch(() => (templates = []));
		listAnalysisTools()
			.then((list) => (tools = list))
			.catch(() => (tools = []));
		loadModelCatalog()
			.then((list) => {
				models = list;
				if (pendingModelId) {
					modelPk = findModel(list, pendingModelId)?.id ?? null;
					pendingModelId = '';
				}
			})
			.catch(() => (models = []));
	});

	// Plans in use for the scope (per-plan job counts help pick one).
	let plansController: AbortController | null = null;
	$effect(() => {
		if (!info.measured) return;
		const scopeExperiment = reportType === 'experiment_report' ? experimentId : '';
		untrack(async () => {
			plansController?.abort();
			const c = new AbortController();
			plansController = c;
			plansLoading = true;
			try {
				const res = await getRankings(
					{ per_page: 10, experiment_id: scopeExperiment || undefined },
					{ signal: c.signal }
				);
				if (!c.signal.aborted) plans = res.comparability?.plans ?? {};
			} catch (e) {
				if (!isAbortError(e) && !c.signal.aborted) plans = {};
			} finally {
				if (plansController === c) plansLoading = false;
			}
		});
	});

	const planItems = $derived.by(() => {
		const items = Object.entries(plans)
			.sort((a, b) => b[1] - a[1])
			.map(([hash, n]) => ({
				value: hash,
				label: `${shortHash(hash, 12)} · ${n} job${n === 1 ? '' : 's'}`
			}));
		if (planHash && !items.some((i) => i.value === planHash)) {
			items.unshift({ value: planHash, label: `${shortHash(planHash, 12)} · from the link` });
		}
		return items;
	});

	const experimentItems = $derived(
		experiments.map((e) => ({
			value: e.id,
			label: e.name,
			description: `${e.status} · ${e.condition_count} conditions`
		}))
	);
	const templateItems = $derived(
		templates.map((t) => ({ value: t.slug, label: t.name, description: t.category }))
	);
	const toolItems = $derived(
		tools.map((t) => ({ value: t.slug, label: t.name, description: `${t.category} · ${t.kind}` }))
	);
	const expiresItems = [
		{ value: '7', label: '7 days' },
		{ value: '30', label: '30 days' },
		{ value: '90', label: '90 days' },
		{ value: '365', label: '1 year' },
		{ value: 'never', label: 'Never' }
	];

	function modelIdOf(pk: number | null): string | undefined {
		return pk == null ? undefined : findModel(models, pk)?.model_id;
	}

	function buildConfig(): { config: ReportConfig; errors: Record<string, string> } {
		const config: ReportConfig = {};
		const errors: Record<string, string> = {};
		if (reportType === 'experiment_report') {
			if (!experimentId) errors.experiment_id = 'Pick an experiment.';
			else config.experiment_id = experimentId;
		}
		if (reportType === 'model_analysis') {
			const id = modelIdOf(modelPk);
			if (!id) errors.model_id = 'Pick a model.';
			else config.model_id = id;
		}
		if (reportType === 'template_comparison') {
			if (!templateSlug) errors.template_slug = 'Pick an app template.';
			else config.template_slug = templateSlug;
			const filter = [...filterModelPks].map(modelIdOf).filter((m): m is string => !!m);
			if (filter.length) config.filter_models = filter;
			if (bundleKey.trim()) config.bundle_key = bundleKey.trim();
			if (promptHash.trim()) config.prompt_hash = promptHash.trim();
		}
		if (reportType === 'tool_analysis') {
			if (toolName) config.tool_name = toolName;
			const id = modelIdOf(filterModelPk);
			if (id) config.filter_model = id;
		}
		if (reportType === 'generation_analytics' || reportType === 'comprehensive') {
			if (days == null || !Number.isInteger(days) || days < 1 || days > 365) {
				errors.days = 'Enter a whole number of days between 1 and 365.';
			} else config.days = days;
		}
		if (info.measured) {
			if (planHash) {
				if (!/^[0-9a-f]{64}$/.test(planHash))
					errors.plan_hash = 'A plan hash has 64 hexadecimal characters.';
				else config.plan_hash = planHash;
			}
			config.exclude_truncated = excludeTruncated;
			config.exclude_invalid = excludeInvalid;
			config.exclude_empty_output = excludeEmpty;
		}
		return { config, errors };
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		const { config, errors } = buildConfig();
		fieldErrors = errors;
		if (Object.keys(errors).length > 0) {
			error = 'Fix the highlighted fields.';
			return;
		}
		submitting = true;
		error = null;
		try {
			const report = await generateReport({
				report_type: reportType,
				config,
				title: title.trim() || undefined,
				description: description.trim() || undefined,
				expires_in_days: expires === 'never' ? null : Number(expires)
			});
			goto(`/reports/${report.report_id}`);
		} catch (e) {
			error = formatApiError(e, 'Could not start the report.');
			submitting = false;
		}
	}

	/**
	 * Hand the number itself to `<input type="number">` (typed as the Input's
	 * string prop): stringifying it would let Svelte rewrite partial input.
	 */
	function numberValue(v: number | null): string {
		return (v ?? '') as unknown as string;
	}

	function fieldClass(key: string): string {
		return fieldErrors[key] ? 'border-destructive' : '';
	}
</script>

{#snippet fieldError(key: string)}
	{#if fieldErrors[key]}<p id="err-{key}" class="text-xs text-danger-fg">{fieldErrors[key]}</p>{/if}
{/snippet}

<div class="mx-auto max-w-4xl space-y-6">
	<PageHeader
		title="New report"
		description="Pick what the report covers. Analysis numbers are taken from one analysis plan; jobs outside it are counted, never treated as clean."
		back={{ href: '/reports', label: 'Reports' }}
	/>

	<form class="space-y-6" onsubmit={submit} novalidate>
		<Card.Root>
			<Card.Header>
				<Card.Title class="text-base">1. Report type</Card.Title>
			</Card.Header>
			<Card.Content>
				<RadioGroup.Root
					bind:value={reportType}
					onValueChange={() => {
						fieldErrors = {};
						error = null;
					}}
					aria-label="Report type"
					class="grid gap-3 sm:grid-cols-2"
					loop
				>
					{#each REPORT_TYPES as t (t)}
						{@const typeInfo = REPORT_TYPE_INFO[t]}
						{@const TypeIcon = typeInfo.icon}
						<RadioGroup.Item
							value={t}
							class={cn(
								'flex cursor-pointer items-start gap-3 rounded-md border border-border p-4 text-left transition-colors duration-150',
								'hover:border-primary/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
								'data-[state=checked]:border-primary data-[state=checked]:bg-primary/5'
							)}
						>
							{#snippet children({ checked })}
								<TypeIcon
									class="mt-0.5 h-4 w-4 shrink-0 {checked
										? 'text-primary'
										: 'text-muted-foreground'}"
									aria-hidden="true"
								/>
								<span class="min-w-0 flex-1">
									<span class="flex items-center gap-2 text-sm font-medium">
										{typeInfo.label}
										{#if typeInfo.measured}
											<span
												class="rounded-full bg-muted px-1.5 text-[11px] font-normal text-muted-foreground"
												>one plan</span
											>
										{/if}
									</span>
									<span class="mt-1 block text-xs text-muted-foreground"
										>{typeInfo.description}</span
									>
								</span>
								{#if checked}<Check class="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />{/if}
							{/snippet}
						</RadioGroup.Item>
					{/each}
				</RadioGroup.Root>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Header>
				<Card.Title class="text-base">2. Scope · {info.label}</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				{#if reportType === 'experiment_report'}
					<div class="space-y-1.5">
						<Label for="rep-experiment">Experiment</Label>
						<Select.Simple
							id="rep-experiment"
							bind:value={experimentId}
							items={experimentItems}
							placeholder={experiments.length ? 'Select an experiment' : 'No experiments yet'}
							class={fieldClass('experiment_id')}
							aria-invalid={!!fieldErrors.experiment_id}
							aria-describedby={fieldErrors.experiment_id ? 'err-experiment_id' : undefined}
						/>
						{@render fieldError('experiment_id')}
					</div>
				{:else if reportType === 'model_analysis'}
					<div class="space-y-1.5">
						<ModelPicker
							mode="single"
							bind:selectedId={modelPk}
							label="Model"
							idPrefix="rep-model"
						/>
						{@render fieldError('model_id')}
					</div>
				{:else if reportType === 'template_comparison'}
					<div class="space-y-1.5">
						<Label for="rep-template">App template</Label>
						<Select.Simple
							id="rep-template"
							bind:value={templateSlug}
							items={templateItems}
							placeholder="Select an app template"
							class={fieldClass('template_slug')}
							aria-invalid={!!fieldErrors.template_slug}
							aria-describedby={fieldErrors.template_slug ? 'err-template_slug' : undefined}
						/>
						{@render fieldError('template_slug')}
					</div>
					<div class="grid gap-4 sm:grid-cols-2">
						<div class="space-y-1.5">
							<Label for="rep-bundle">Bundle key (optional)</Label>
							<Input id="rep-bundle" bind:value={bundleKey} placeholder="e.g. flask-react@3" />
							<p class="text-xs text-muted-foreground">
								One prompt bundle version for a like-for-like comparison.
							</p>
						</div>
						<div class="space-y-1.5">
							<Label for="rep-prompt">Prompt hash (optional)</Label>
							<Input id="rep-prompt" bind:value={promptHash} class="font-mono" />
						</div>
					</div>
					<div class="space-y-1.5">
						<ModelPicker
							mode="multi"
							bind:selectedIds={filterModelPks}
							label="Limit to models (optional)"
							idPrefix="rep-filter-models"
							maxHeight="max-h-60"
						/>
						<p class="text-xs text-muted-foreground">
							Leave empty to include every model that generated this template.
						</p>
					</div>
				{:else if reportType === 'tool_analysis'}
					<div class="space-y-1.5">
						<Label for="rep-tool">Tool</Label>
						<Select.Simple
							id="rep-tool"
							bind:value={toolName}
							items={toolItems}
							placeholder="All tools"
							clearLabel="All tools"
						/>
					</div>
					<div class="space-y-1.5">
						<ModelPicker
							mode="single"
							bind:selectedId={filterModelPk}
							label="Only runs of jobs by this model (optional)"
							idPrefix="rep-tool-model"
							maxHeight="max-h-60"
						/>
						{#if filterModelPk != null}
							<Button variant="ghost" size="sm" onclick={() => (filterModelPk = null)}
								>Clear model filter</Button
							>
						{/if}
					</div>
				{:else}
					<div class="max-w-xs space-y-1.5">
						<Label for="rep-days">Window (days)</Label>
						<Input
							id="rep-days"
							type="number"
							inputmode="numeric"
							min="1"
							max="365"
							step="1"
							value={numberValue(days)}
							oninput={(e: Event & { currentTarget: HTMLInputElement }) => {
								const v = e.currentTarget.valueAsNumber;
								days = Number.isNaN(v) ? null : v;
							}}
							class={fieldClass('days')}
							aria-invalid={!!fieldErrors.days}
							aria-describedby={fieldErrors.days ? 'err-days' : undefined}
						/>
						{@render fieldError('days')}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>

		{#if info.measured}
			<Card.Root>
				<Card.Header>
					<Card.Title class="text-base">3. Analysis settings</Card.Title>
					<Card.Description>
						Runs are comparable only under one frozen plan. Auto picks the plan that completely
						measured the most of the report's jobs.
					</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-4">
					<div class="space-y-1.5">
						<Label for="rep-plan">Analysis plan</Label>
						<Select.Simple
							id="rep-plan"
							bind:value={planHash}
							items={planItems}
							placeholder={plansLoading
								? 'Loading plans…'
								: 'Auto: the plan that measured most jobs'}
							clearLabel="Auto: the plan that measured most jobs"
							class={fieldClass('plan_hash')}
							aria-invalid={!!fieldErrors.plan_hash}
							aria-describedby={fieldErrors.plan_hash ? 'err-plan_hash' : undefined}
						/>
						{@render fieldError('plan_hash')}
						<p class="text-xs text-muted-foreground">
							Job counts are the plans' canonical runs in {reportType === 'experiment_report' &&
							experimentId
								? 'this experiment'
								: 'all your jobs'}.
						</p>
					</div>
					<fieldset class="space-y-2">
						<legend class="text-sm font-medium">Exclude from the trials</legend>
						<label class="flex items-start gap-2 text-sm">
							<Checkbox bind:checked={excludeTruncated} class="mt-0.5" />
							<span
								>Truncated generations <span class="block text-xs text-muted-foreground"
									>output cut off by the token or turn limit</span
								></span
							>
						</label>
						<label class="flex items-start gap-2 text-sm">
							<Checkbox bind:checked={excludeInvalid} class="mt-0.5" />
							<span
								>Invalid samples <span class="block text-xs text-muted-foreground"
									>the agent's validity check says exclude</span
								></span
							>
						</label>
						<label class="flex items-start gap-2 text-sm">
							<Checkbox bind:checked={excludeEmpty} class="mt-0.5" />
							<span
								>Empty output <span class="block text-xs text-muted-foreground"
									>the model wrote no code</span
								></span
							>
						</label>
					</fieldset>
				</Card.Content>
			</Card.Root>
		{/if}

		<Card.Root>
			<Card.Header>
				<Card.Title class="text-base">{info.measured ? '4' : '3'}. Details</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				<div class="space-y-1.5">
					<Label for="rep-title">Title (optional)</Label>
					<Input
						id="rep-title"
						bind:value={title}
						placeholder="Generated from the type and scope if blank"
						maxlength={200}
					/>
				</div>
				<div class="space-y-1.5">
					<Label for="rep-desc">Description (optional)</Label>
					<Textarea id="rep-desc" bind:value={description} rows={2} />
				</div>
				<div class="max-w-xs space-y-1.5">
					<Label for="rep-expires">Keep for</Label>
					<Select.Simple id="rep-expires" bind:value={expires} items={expiresItems} />
				</div>
			</Card.Content>
		</Card.Root>

		{#if error}<ErrorAlert message={error} />{/if}

		<div class="flex flex-wrap justify-end gap-2">
			<Button variant="outline" href="/reports">Cancel</Button>
			<Button type="submit" disabled={submitting}>
				{#if submitting}<LoaderCircle
						class="h-4 w-4 animate-spin motion-reduce:animate-none"
					/>{/if}
				Generate report
			</Button>
		</div>
	</form>
</div>
