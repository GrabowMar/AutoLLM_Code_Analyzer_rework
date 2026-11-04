<script lang="ts">
	/**
	 * Generate app — one agentic app from one choice per factor (task, stack, style, auth, prose, model,
	 * harness, Budget preset; advanced: prompt set, evaluation level, seed). The form is the design the
	 * experiment builder would produce for those choices (`$lib/components/generate/quick-design`).
	 *
	 * Pricing: every complete form is sent to `POST /studies/adhoc/` with `quick` and without `launch` (a dry
	 * run: resolved and priced, nothing is created), debounced and aborted when superseded; the estimate and
	 * the plan's issues are shown, API errors inline under their field. "Generate" re-sends the same design
	 * (same seed) with `launch` and `quick`, confirming the p90 the researcher has just seen (`confirm_usd`;
	 * a higher p90 than the shown one asks again), and opens the sample, which follows the run live.
	 * Quick generations are experiments of one cell, hidden from the experiments list by default.
	 */
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { goto } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import NumberInput from '$lib/components/slots/NumberInput.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { createAdhocStudy } from '$lib/api/v3/studies';
	import { EVAL_LEVELS, type AdhocOut, type Estimate, type Issue } from '$lib/api/v3/types';
	import { libraryOptions, modelOptions, type OptionItem } from '$lib/components/slots/options';
	import { launchQuote, type LaunchQuote } from '$lib/components/slots/parent-design';
	import { FACTOR_BY_KEY, MODE_DEFAULTS, randomSeed } from '$lib/components/studies/builder/design';
	import { estimateBasisTotals } from '$lib/components/studies/builder/policy';
	import { issueTitle } from '$lib/components/studies/builder/issues';
	import { formatUsd } from '$lib/components/studies/format';
	import {
		buildQuickDesign,
		emptyQuickAppForm,
		quickStudyName,
		validateQuickAppForm,
		type QuickAppForm,
	} from '$lib/components/generate/quick-design';

	type Field = keyof QuickAppForm;

	const enumItems = (key: 'auth_mode' | 'prose_variant'): OptionItem[] => {
		const source = FACTOR_BY_KEY[key].source;
		return source.type === 'enum' ? source.values.map((v) => ({ value: v.value, label: v.label })) : [];
	};
	const AUTH_ITEMS = enumItems('auth_mode');
	const PROSE_ITEMS: OptionItem[] = enumItems('prose_variant').map((item) => ({
		...item,
		description: {
			minimal: 'The brief states the least; most checks are unstated',
			standard: 'Names every endpoint',
			exhaustive: 'States every requirement the checks test',
		}[item.value],
	}));
	const LEVEL_ITEMS: OptionItem[] = EVAL_LEVELS.map((level) => ({
		value: level,
		label: level === 'security' ? 'Security (default)' : level.charAt(0).toUpperCase() + level.slice(1),
		description: {
			none: 'Nothing is evaluated: every outcome is not measured',
			static: 'Lock, static adherence and default SAST; no build',
			build: 'Lock, build and adherence',
			functional: 'Build, start, functional checks, baseline',
			security: 'Functional plus probes and default SAST',
			full: 'Security plus dynamic analysis',
		}[level],
	}));

	const PRICE_DEBOUNCE_MS = 500;

	// `?model=` and `?task=` pre-fill (model pages, the old sample generator links).
	let form = $state<QuickAppForm>(
		emptyQuickAppForm({
			model: page.url.searchParams.get('model') ?? '',
			task: page.url.searchParams.get('task') ?? '',
		}),
	);
	// Drawn once: the priced design and the launched one share it unless the researcher sets a seed.
	const seed = randomSeed();
	let touched = $state(false);
	let advancedOpen = $state(false);

	let options = $state<Record<string, OptionItem[]> | null>(null);
	let optionsError = $state('');

	/** The last dry run, for the form it priced. */
	let priced = $state<{ key: string; out: AdhocOut } | null>(null);
	let pricing = $state(false);
	let priceError = $state('');
	/** API errors mapped onto fields (`invalid_design` paths, issue paths). */
	let apiErrors = $state<Partial<Record<Field, string>>>({});
	let apiIssues = $state<Issue[]>([]);

	let generating = $state(false);
	let generateError = $state('');
	/** A p90 above the one shown: the server's quote, for the form it priced. */
	let quote = $state<{ key: string; quote: LaunchQuote } | null>(null);

	const design = $derived(buildQuickDesign(form, seed));
	const formKey = $derived(JSON.stringify(design));
	const localErrors = $derived(validateQuickAppForm(form));
	const complete = $derived(Object.keys(localErrors).length === 0);
	const current = $derived(priced && priced.key === formKey ? priced.out : null);
	const estimate = $derived((current?.estimate as Estimate | null | undefined) ?? null);
	const totals = $derived(estimate ? estimateBasisTotals(estimate) : null);
	const issues = $derived(current ? (current.issues ?? []) : apiIssues);
	const blocking = $derived(issues.filter((i) => i.severity === 'error'));
	const shownIssues = $derived(issues.filter((i) => i.severity !== 'info'));
	const currentQuote = $derived(quote && quote.key === formKey ? quote.quote : null);
	const canGenerate = $derived(complete && current !== null && blocking.length === 0 && !generating && !pricing);

	function errorFor(field: Field): string {
		if (apiErrors[field]) return apiErrors[field] ?? '';
		return touched ? (localErrors[field] ?? '') : '';
	}

	function set<K extends Field>(key: K, value: QuickAppForm[K]) {
		form = { ...form, [key]: value };
	}

	// ── Options ────────────────────────────────────────────────────────────────

	async function loadOptions() {
		optionsError = '';
		try {
			const [tasks, stacks, styles, harnesses, policies, promptSets, models] = await Promise.all([
				libraryOptions('task', true),
				libraryOptions('stack'),
				libraryOptions('style'),
				libraryOptions('harness_preset'),
				libraryOptions('resource_policy'),
				libraryOptions('prompt_set'),
				modelOptions(true),
			]);
			options = { tasks, stacks, styles, harnesses, policies, promptSets, models };
		} catch (e) {
			optionsError = formatApiError(e, 'Could not load the library and the models.');
		}
	}

	$effect(() => {
		untrack(() => void loadOptions());
	});

	// ── Pricing (dry run) ──────────────────────────────────────────────────────

	/** `invalid_design` / issue paths → the form field they name. */
	function fieldOf(path: string | null | undefined): Field | null {
		if (!path) return null;
		const p = path.toLowerCase();
		const map: [string, Field][] = [
			['units.tasks', 'task'],
			['model', 'model'],
			['harness', 'harness_preset'],
			['stack', 'stack'],
			['style', 'style'],
			['auth', 'auth_mode'],
			['prose', 'prose_variant'],
			['prompt', 'prompt_set'],
			['resource_policy', 'resource_policy'],
			['evaluation', 'eval_level'],
			['seed', 'base_seed'],
		];
		return map.find(([needle]) => p.includes(needle))?.[1] ?? null;
	}

	function applyApiError(e: unknown) {
		const body = (e ?? {}) as { detail?: unknown; path?: string | null; issues?: Issue[]; data?: { issues?: Issue[] } };
		const list = Array.isArray(body.issues) ? body.issues : Array.isArray(body.data?.issues) ? body.data.issues : [];
		const fields: Partial<Record<Field, string>> = {};
		const detail = formatApiError(e, 'Could not price this app.');
		const field = fieldOf(body.path);
		if (field) fields[field] = detail;
		for (const issue of list) {
			const f = fieldOf(issue.path);
			if (f && !fields[f]) fields[f] = issue.message || issueTitle(issue.code);
		}
		apiErrors = fields;
		apiIssues = list;
		return field ? '' : detail;
	}

	async function price(key: string, body: ReturnType<typeof buildQuickDesign>, signal: AbortSignal) {
		pricing = true;
		try {
			const out = await createAdhocStudy({ design: body, name: quickStudyName(form), launch: false, quick: true }, { signal });
			if (signal.aborted) return;
			priced = { key, out };
			priceError = '';
			apiErrors = {};
			apiIssues = [];
		} catch (e) {
			if (isAbortError(e) || signal.aborted) return;
			priced = null;
			priceError = applyApiError(e);
		} finally {
			if (!signal.aborted) pricing = false;
		}
	}

	$effect(() => {
		const key = formKey;
		const body = design;
		if (!complete || !options) return;
		if (untrack(() => priced?.key === key)) return;
		const controller = new AbortController();
		const timer = setTimeout(() => void price(key, body, controller.signal), PRICE_DEBOUNCE_MS);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	// Any edit drops errors that described the previous form.
	$effect(() => {
		void formKey;
		untrack(() => {
			if (Object.keys(apiErrors).length) apiErrors = {};
			generateError = '';
		});
	});

	// ── Generate ──────────────────────────────────────────────────────────────

	async function generate(confirmUsd: number | null) {
		touched = true;
		if (!canGenerate || !totals) return;
		const key = formKey;
		generating = true;
		generateError = '';
		try {
			const out = await createAdhocStudy({
				design,
				name: quickStudyName(form),
				launch: true,
				quick: true,
				confirm_usd: confirmUsd ?? totals.p90,
			});
			const runId = out.run_ids[0];
			if (!runId) {
				generateError = 'The app was planned but no run was launched; see the issues.';
				apiIssues = out.issues ?? [];
				return;
			}
			await goto(`/samples/${encodeURIComponent(runId)}`);
		} catch (e) {
			const q = launchQuote(e);
			if (q) {
				// The estimate moved above what was shown: nothing launched; ask again with the new figure.
				quote = { key, quote: q };
				return;
			}
			generateError = applyApiError(e) || 'Could not generate the app; see the highlighted fields.';
		} finally {
			generating = false;
		}
	}
</script>

{#snippet field(id: string, label: string, key: Field, items: OptionItem[], help: string, placeholder = 'Pick one', clearLabel?: string)}
	{@const message = errorFor(key)}
	<div class="space-y-1">
		<Label for={id}>{label}</Label>
		<Select.Simple
			{id}
			value={String(form[key] ?? '')}
			onValueChange={(v) => set(key, v as never)}
			{items}
			{placeholder}
			{clearLabel}
			aria-invalid={!!message}
			aria-describedby={`${id}-help`}
		/>
		{#if message}
			<p id={`${id}-help`} class="text-[11px] text-danger-fg">{message}</p>
		{:else}
			<p id={`${id}-help`} class="text-[11px] text-muted-foreground">{help}</p>
		{/if}
	</div>
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="Generate app"
		description="One app from one task, stack and model: a single agentic run, priced before it starts."
	>
		{#snippet help()}
			<p class="text-sm">
				A quick generation is an experiment with one cell (one condition, one task, one repeat). It is hidden from
				the Experiments list unless you tick “Show quick generations”. To compare models or settings, design an
				experiment instead.
			</p>
		{/snippet}
		{#snippet actions()}
			<Button href="/experiments/new" size="sm" variant="outline">Design an experiment</Button>
		{/snippet}
	</PageHeader>

	{#if optionsError}<ErrorAlert message={optionsError} onretry={loadOptions} />{/if}

	{#if !options && !optionsError}
		<LoadingState label="loading the library" />
	{:else if options}
		<form
			class="max-w-2xl space-y-4"
			onsubmit={(e) => {
				e.preventDefault();
				void generate(null);
			}}
		>
			<Card.Root>
				<Card.Header>
					<Card.Title>What to build</Card.Title>
					<Card.Description>Each choice is one level of the experiment builder's factors.</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-4">
					{@render field('gen-task', 'Task', 'task', options.tasks, 'The app specification the run implements.')}
					{@render field('gen-stack', 'Stack', 'stack', options.stacks, FACTOR_BY_KEY.stack_profile.help)}
					{@render field('gen-style', 'Style', 'style', options.styles, FACTOR_BY_KEY.style.help)}
					{@render field('gen-auth', 'Auth mode', 'auth_mode', AUTH_ITEMS, FACTOR_BY_KEY.auth_mode.help)}
					{@render field('gen-prose', 'Prose variant', 'prose_variant', PROSE_ITEMS, FACTOR_BY_KEY.prose_variant.help)}
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title>Who builds it</Card.Title>
					<Card.Description>Models that support tool calling, cheapest first.</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-4">
					{@render field('gen-model', 'Model', 'model', options.models, FACTOR_BY_KEY.model.help, 'Pick a model')}
					{@render field('gen-harness', 'Harness', 'harness_preset', options.harnesses, FACTOR_BY_KEY.harness_preset.help)}
					{@render field(
						'gen-policy',
						'Budget preset',
						'resource_policy',
						options.policies,
						'Run, token and time caps, enforced outside the model; never part of the prompt.',
					)}

					<details class="rounded-md border border-border" bind:open={advancedOpen}>
						<summary class="cursor-pointer px-3 py-2 text-sm font-medium">Advanced</summary>
						<div class="space-y-4 border-t border-border/60 px-3 py-3">
							{@render field(
								'gen-prompts',
								'Prompt set',
								'prompt_set',
								options.promptSets,
								FACTOR_BY_KEY.prompt_set.help,
								`default: ${MODE_DEFAULTS.app.prompt_set}`,
								'Default',
							)}
							{@render field('gen-level', 'Evaluation level', 'eval_level', LEVEL_ITEMS, 'What is measured after the run (the default evaluation suite).')}
							<div class="space-y-1">
								<Label for="gen-seed">Seed</Label>
								<NumberInput
									id="gen-seed"
									min={0}
									step={1}
									placeholder={`random (${seed})`}
									value={form.base_seed}
									invalid={!!errorFor('base_seed')}
									onvaluechange={(v) => set('base_seed', v)}
								/>
								{#if errorFor('base_seed')}
									<p class="text-[11px] text-danger-fg">{errorFor('base_seed')}</p>
								{:else}
									<p class="text-[11px] text-muted-foreground">Same seed and choices, same pinned setup (the model may still differ).</p>
								{/if}
							</div>
						</div>
					</details>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title>Cost</Card.Title>
					<Card.Description>Estimated from the model's prices and earlier runs; the Budget preset caps the real spend.</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3 text-sm" aria-live="polite">
					{#if !complete}
						<p class="text-muted-foreground">Pick a task, a stack and a model to see the estimate.</p>
					{:else if pricing && !current}
						<p class="text-muted-foreground">
							<LoaderCircle class="mr-1 inline h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />Pricing…
						</p>
					{:else if priceError}
						<ErrorAlert message={priceError} />
					{:else if Object.keys(apiErrors).length}
						<p class="text-danger-fg">The API refused this choice: see the highlighted field.</p>
					{:else if current && totals}
						<dl class="grid grid-cols-2 gap-3 sm:grid-cols-3">
							<div>
								<dt class="section-label">Expected</dt>
								<dd class="font-mono text-lg font-medium tabular-nums">{formatUsd(totals.expected)}</dd>
							</div>
							<div>
								<dt class="section-label">Up to (p90)</dt>
								<dd class="font-mono text-lg font-medium tabular-nums">{formatUsd(totals.p90)}</dd>
							</div>
							{#if estimate?.time}
								<div>
									<dt class="section-label">Wall time</dt>
									<dd class="font-mono text-lg font-medium tabular-nums">
										{Math.round(estimate.time.expected_wall_s_low / 60)}–{Math.round(estimate.time.expected_wall_s_high / 60)} min
									</dd>
								</div>
							{/if}
						</dl>
						{#if totals.basis === 'list'}
							<p class="text-xs text-muted-foreground">At list price: this endpoint bills $0, the cap counts list price.</p>
						{/if}
						{#if pricing}<p class="text-xs text-muted-foreground">Updating…</p>{/if}
					{/if}

					{#if shownIssues.length}
						<ul class="space-y-1.5 text-xs">
							{#each shownIssues as issue, i (i)}
								<li class="flex flex-wrap items-start gap-1.5">
									<StatusBadge status={issue.severity} tone={issue.severity === 'error' ? 'danger' : 'warning'} label={issueTitle(issue.code)} class="normal-case" />
									<span>{issue.message}</span>
								</li>
							{/each}
						</ul>
					{/if}

					{#if currentQuote}
						<div class="space-y-1 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs" role="status">
							<p class="font-medium">The estimate went up: confirm before generating</p>
							<p>
								Now <b class="font-mono tabular-nums">{formatUsd(currentQuote.expected_total)}</b> expected, up to
								<b class="font-mono tabular-nums">{formatUsd(currentQuote.p90_total)}</b> (p90), above the Budget preset's confirmation threshold of
								<span class="font-mono tabular-nums">{formatUsd(currentQuote.threshold)}</span>. Nothing has been launched.
							</p>
						</div>
					{/if}
					{#if generateError}<ErrorAlert message={generateError} />{/if}
				</Card.Content>
				<Card.Footer class="flex flex-wrap justify-end gap-2 border-t border-border">
					{#if currentQuote}
						{@const q = currentQuote}
						<Button size="sm" type="button" disabled={generating} onclick={() => void generate(q.p90_total)}>
							{#if generating}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Sparkles class="mr-1.5 h-3.5 w-3.5" />{/if}
							Generate for ≤ {formatUsd(q.p90_total)}
						</Button>
					{:else}
						<Button size="sm" type="submit" disabled={!canGenerate} title={blocking.length ? 'Fix the blocking issues first' : undefined}>
							{#if generating}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Sparkles class="mr-1.5 h-3.5 w-3.5" />{/if}
							{totals && current ? `Generate (≤ ${formatUsd(totals.p90)})` : 'Generate'}
						</Button>
					{/if}
				</Card.Footer>
			</Card.Root>
		</form>
	{/if}
</div>
