<script lang="ts">
	/**
	 * /library/dry-run — Generation dry run: pick one choice per factor (task, stack, style, auth mode,
	 * prose variant, prompt set, model, harness, Budget preset) and see everything a generation would
	 * receive, without generating: the rendered prompts with the library entity that owns each span, the
	 * effective roles, parameters and harness, the starter files, identity keys, the estimate and the full
	 * run manifest.
	 *
	 * The form is the quick-generation design (`$lib/components/generate/quick-design`) sent to
	 * `POST /studies/dry-run/`: resolved by the same planner, then rolled back (no study, plan or run is
	 * kept, no LLM is called). The choices are mirrored in the query string, so a dry run is a link.
	 */
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import { Input } from '$lib/components/ui/input';
	import ModelPicker from '$lib/components/studies/shared/ModelPicker.svelte';
	import { loadModelCatalog } from '$lib/components/studies/shared/catalog';
	import { CellDetailView } from '$lib/components/studies';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Copy from '@lucide/svelte/icons/copy';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { dryRunDesign } from '$lib/api/v3/studies';
	import { EVAL_LEVELS, type DryRunOut, type EvalLevel, type Issue } from '$lib/api/v3/types';
	import type { LLMModelSummary } from '$lib/api/client';
	import { libraryOptions, type OptionItem } from '$lib/components/slots/options';
	import { FACTOR_BY_KEY, randomSeed } from '$lib/components/studies/builder/design';
	import { estimateBasisTotals } from '$lib/components/studies/builder/policy';
	import { issueTitle } from '$lib/components/studies/builder/issues';
	import { formatTokenCount, formatUsd, shortHash } from '$lib/components/studies/format';
	import { buildQuickDesign, emptyQuickAppForm, validateQuickAppForm, type QuickAppForm } from '$lib/components/generate/quick-design';
	import { cn } from '$lib/utils';

	type Field = keyof QuickAppForm;

	/** Form field ↔ query parameter (a dry run is a shareable link). */
	const PARAMS: [Field, string][] = [
		['task', 'task'],
		['stack', 'stack'],
		['style', 'style'],
		['auth_mode', 'auth'],
		['prose_variant', 'prose'],
		['prompt_set', 'prompt_set'],
		['model', 'model'],
		['harness_preset', 'harness'],
		['resource_policy', 'budget'],
		['eval_level', 'eval'],
	];

	function fromQuery(): Partial<QuickAppForm> {
		const out: Partial<Record<Field, string>> = {};
		for (const [field, param] of PARAMS) {
			const value = page.url.searchParams.get(param);
			if (value !== null) out[field] = value;
		}
		const seed = Number(page.url.searchParams.get('seed'));
		return {
			...(out as Partial<QuickAppForm>),
			eval_level: (EVAL_LEVELS as readonly string[]).includes(out.eval_level ?? '') ? (out.eval_level as EvalLevel) : 'security',
			base_seed: page.url.searchParams.has('seed') && Number.isInteger(seed) ? seed : null,
		};
	}

	let form = $state<QuickAppForm>(emptyQuickAppForm(fromQuery()));
	const seed = randomSeed();
	let seedText = $state(form.base_seed === null ? '' : String(form.base_seed));
	let touched = $state(false);

	const enumItems = (key: 'auth_mode' | 'prose_variant'): OptionItem[] => {
		const source = FACTOR_BY_KEY[key].source;
		return source.type === 'enum' ? source.values.map((v) => ({ value: v.value, label: v.label })) : [];
	};
	const AUTH_ITEMS = enumItems('auth_mode');
	const PROSE_ITEMS = enumItems('prose_variant');
	const LEVEL_ITEMS: OptionItem[] = EVAL_LEVELS.map((level) => ({ value: level, label: level.charAt(0).toUpperCase() + level.slice(1) }));

	// ── Library data ─────────────────────────────────────────────────────────────
	interface Lib {
		tasks: OptionItem[];
		stacks: OptionItem[];
		styles: OptionItem[];
		promptSets: OptionItem[];
		harnesses: OptionItem[];
		policies: OptionItem[];
		models: LLMModelSummary[];
	}
	let lib = $state<Lib | null>(null);
	let optionsError = $state('');

	async function loadOptions() {
		optionsError = '';
		try {
			const [tasks, stacks, styles, promptSets, harnesses, policies, models] = await Promise.all([
				libraryOptions('task', true),
				libraryOptions('stack'),
				libraryOptions('style'),
				libraryOptions('prompt_set'),
				libraryOptions('harness_preset'),
				libraryOptions('resource_policy'),
				loadModelCatalog(),
			]);
			lib = { tasks, stacks, styles, promptSets, harnesses, policies, models };
		} catch (e) {
			optionsError = formatApiError(e, 'Could not load the library and the models.');
		}
	}
	$effect(() => {
		untrack(() => void loadOptions());
	});

	// Model: the picker works on pks, the design on `model_id`.
	let modelPk = $state<number | null>(null);
	$effect(() => {
		const id = form.model;
		const models = lib?.models;
		if (!models) return;
		untrack(() => {
			const pk = models.find((m) => m.model_id === id || m.canonical_slug === id)?.id ?? null;
			if (pk !== modelPk) modelPk = pk;
		});
	});
	function onModelPicked() {
		form.model = lib?.models.find((x) => x.id === modelPk)?.model_id ?? '';
	}

	function onSeed(value: string) {
		seedText = value;
		const n = Number(value);
		form.base_seed = value.trim() === '' ? null : Number.isFinite(n) ? n : NaN;
	}

	// ── Dry run ──────────────────────────────────────────────────────────────────
	const design = $derived(buildQuickDesign(form, seed));
	const localErrors = $derived(validateQuickAppForm(form));
	const complete = $derived(Object.keys(localErrors).length === 0);
	const designKey = $derived(JSON.stringify(design));

	let result = $state<{ key: string; out: DryRunOut } | null>(null);
	let running = $state(false);
	let runError = $state('');
	let errorIssues = $state<Issue[]>([]);
	let selected = $state(0);
	let controller: AbortController | null = null;

	const stale = $derived(result !== null && result.key !== designKey);
	const plan = $derived(result?.out.plan ?? null);
	const totals = $derived(plan?.estimate ? estimateBasisTotals(plan.estimate) : null);
	const issues = $derived((plan?.issues ?? errorIssues).filter((i) => i.severity !== 'info'));
	const cell = $derived(result?.out.cells[selected] ?? null);

	function syncQuery() {
		const url = new URL(page.url);
		for (const [field, param] of PARAMS) {
			const value = String(form[field] ?? '').trim();
			if (value) url.searchParams.set(param, value);
			else url.searchParams.delete(param);
		}
		if (form.base_seed !== null) url.searchParams.set('seed', String(form.base_seed));
		else url.searchParams.delete('seed');
		replaceState(url, page.state);
	}

	async function run() {
		touched = true;
		if (!complete) return;
		controller?.abort();
		const ctrl = new AbortController();
		controller = ctrl;
		const key = designKey;
		running = true;
		runError = '';
		errorIssues = [];
		syncQuery();
		try {
			const out = await dryRunDesign({ design }, { signal: ctrl.signal });
			if (ctrl.signal.aborted) return;
			result = { key, out };
			selected = 0;
		} catch (e) {
			if (isAbortError(e) || ctrl.signal.aborted) return;
			const body = (e ?? {}) as { issues?: Issue[]; data?: { issues?: Issue[] } };
			errorIssues = Array.isArray(body.issues) ? body.issues : Array.isArray(body.data?.issues) ? body.data.issues : [];
			runError = formatApiError(e, 'The dry run failed.');
			result = null;
		} finally {
			if (controller === ctrl) running = false;
		}
	}

	let copied = $state('');
	async function copy(label: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = label;
			setTimeout(() => (copied = ''), 1500);
		} catch {
			copied = '';
		}
	}

	const errorFor = (field: Field) => (touched ? (localErrors[field] ?? '') : '');
	const generateHref = $derived(
		`/generate?${new URLSearchParams({ ...(form.task ? { task: form.task } : {}), ...(form.model ? { model: form.model } : {}) }).toString()}`,
	);
	const starterTree = $derived(cell?.starter_tree ?? []);
	const STATUS_TONE: Record<string, string> = { error: 'text-destructive', warning: 'text-warning-fg' };
</script>

{#snippet fieldError(field: Field)}
	{#if errorFor(field)}<p class="text-xs text-destructive">{errorFor(field)}</p>{/if}
{/snippet}

{#snippet pick(field: Field, label: string, items: OptionItem[], opts: { placeholder?: string; clearLabel?: string } = {})}
	<div class="space-y-1">
		<Label for="dr-{field}">{label}</Label>
		<Select.Simple
			id="dr-{field}"
			size="sm"
			value={String(form[field] ?? '')}
			items={items}
			placeholder={opts.placeholder ?? 'Select…'}
			clearLabel={opts.clearLabel}
			aria-invalid={errorFor(field) ? true : undefined}
			onValueChange={(v) => ((form as unknown as Record<string, unknown>)[field] = v)}
		/>
		{@render fieldError(field)}
	</div>
{/snippet}

<div class="space-y-4">
	<PageHeader
		title="Generation dry run"
		description="Everything a generation would receive — rendered prompts, effective roles and parameters, harness, starter files, identity and estimate — without calling a model or keeping anything."
		back={{ href: '/library', label: 'Library' }}
	>
		{#snippet help()}
			<p>
				The choices become the same one-app design the “Generate app” page sends. The planner resolves it exactly as for a real run (pinning
				every library entity to <span class="font-mono">slug@version</span>, rendering the prompt set in the sandboxed Jinja context, applying
				the Budget preset and the model's parameter rules), then the transaction is rolled back. No study, plan or run is created and no LLM
				is called.
			</p>
		{/snippet}
		{#snippet actions()}
			<Button variant="outline" size="sm" href={generateHref}><Sparkles />Generate app…</Button>
		{/snippet}
	</PageHeader>

	{#if optionsError}
		<ErrorAlert message={optionsError} onretry={loadOptions} />
	{:else if !lib}
		<LoadingState label="loading the library" />
	{:else}
		<div class="grid gap-4 xl:grid-cols-[22rem_minmax(0,1fr)]">
			<form
				class="space-y-3 self-start rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)] xl:sticky xl:top-[4.25rem]"
				onsubmit={(e) => {
					e.preventDefault();
					void run();
				}}
			>
				<p class="section-label">What to build</p>
				{@render pick('task', 'Task', lib.tasks, { placeholder: 'Pick a task' })}
				{@render pick('stack', 'Stack', lib.stacks, { placeholder: 'Pick a stack' })}
				{@render pick('style', 'Style', lib.styles, { clearLabel: 'Mode default' })}
				<div class="grid grid-cols-2 gap-2">
					{@render pick('auth_mode', 'Auth mode', AUTH_ITEMS, { clearLabel: 'Mode default' })}
					{@render pick('prose_variant', 'Prose', PROSE_ITEMS, { clearLabel: 'Mode default' })}
				</div>
				{@render pick('prompt_set', 'Prompt set', lib.promptSets, { placeholder: 'Mode default (app-default)', clearLabel: 'Mode default' })}

				<p class="section-label border-t border-border/60 pt-3">Who builds it</p>
				<div class="space-y-1">
					<Label>Model</Label>
					<ModelPicker
						mode="single"
						bind:selectedId={modelPk}
						onchange={onModelPicked}
						requireTools
						groupBy="endpoint"
						label=""
						aria-label="Model"
						idPrefix="dr-model"
						maxHeight="max-h-56"
					/>
					{@render fieldError('model')}
				</div>
				{@render pick('harness_preset', 'Harness', lib.harnesses, { placeholder: 'Pick a harness' })}
				{@render pick('resource_policy', 'Budget preset', lib.policies, { placeholder: 'Pick a Budget preset' })}
				<div class="grid grid-cols-2 gap-2">
					{@render pick('eval_level', 'Evaluation', LEVEL_ITEMS)}
					<div class="space-y-1">
						<Label for="dr-seed">Seed</Label>
						<Input id="dr-seed" class="h-8 font-mono text-xs" inputmode="numeric" placeholder="random" value={seedText} oninput={(e) => onSeed(e.currentTarget.value)} />
						{@render fieldError('base_seed')}
					</div>
				</div>

				<Button type="submit" class="w-full" disabled={running}>
					{#if running}<LoaderCircle class="animate-spin" />Resolving…{:else}<FlaskConical />Run dry run{/if}
				</Button>
				<p class="text-[11px] text-muted-foreground">Nothing is created and no model is called.</p>
			</form>

			<div class="min-w-0 space-y-4">
				{#if runError}
					<ErrorAlert message={runError} onretry={run} />
				{/if}

				{#if !result && !runError && !running}
					<EmptyState
						icon={FlaskConical}
						title="No dry run yet"
						description="Pick a task, stack, model, harness and Budget preset, then run the dry run to see exactly what the generation would be given."
					/>
				{:else if running && !result}
					<LoadingState label="resolving the plan and rendering the prompts" />
				{/if}

				{#if result && plan}
					<section
						class={cn('rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]', (stale || running) && 'opacity-60')}
						aria-label="Plan summary"
					>
						{#if stale}
							<p class="mb-2 text-xs text-warning-fg">The form changed since this dry run; run it again to refresh.</p>
						{/if}
						<dl class="grid gap-x-6 gap-y-2 text-xs sm:grid-cols-2 lg:grid-cols-4">
							<div>
								<dt class="text-muted-foreground">Plan hash</dt>
								<dd class="flex items-center gap-1 font-mono">
									{shortHash(plan.plan_hash)}
									<button type="button" class="text-muted-foreground hover:text-foreground" title="Copy plan hash" onclick={() => copy('hash', plan.plan_hash)}>
										<Copy class="size-3" /><span class="sr-only">Copy plan hash</span>
									</button>
									{#if copied === 'hash'}<span class="text-muted-foreground">copied</span>{/if}
								</dd>
							</div>
							<div>
								<dt class="text-muted-foreground">Cells</dt>
								<dd class="font-mono">
									{plan.n_cells}
									{#if Object.keys(plan.n_cells_by_stage).length > 1}
										<span class="text-muted-foreground">({Object.entries(plan.n_cells_by_stage).map(([s, n]) => `${n} ${s}`).join(', ')})</span>
									{/if}
								</dd>
							</div>
							<div>
								<dt class="text-muted-foreground">Expected cost{totals?.basis === 'list' ? ' (list)' : ''}</dt>
								<dd class="font-mono">{totals ? `${formatUsd(totals.expected)} · p90 ${formatUsd(totals.p90)}` : '—'}</dd>
							</div>
							<div>
								<dt class="text-muted-foreground">Expected tokens</dt>
								<dd class="font-mono">{plan.estimate ? formatTokenCount(plan.estimate.expected_tokens_total) : '—'}</dd>
							</div>
						</dl>
					</section>
				{/if}

				{#if issues.length}
					<section class="rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]" aria-labelledby="dr-issues">
						<h2 id="dr-issues" class="section-label mb-2">Plan issues</h2>
						<ul class="space-y-1 text-xs">
							{#each issues as issue, i (`${issue.code}-${i}`)}
								<li>
									<span class={cn('font-medium', STATUS_TONE[issue.severity])}>{issueTitle(issue.code)}</span>
									{#if issue.message}<span class="text-muted-foreground"> — {issue.message}</span>{/if}
								</li>
							{/each}
						</ul>
					</section>
				{/if}

				{#if result}
					{#if result.out.cells.length === 0}
						<EmptyState compact title="No cells" description="The plan resolved to no cells; see the issues above." />
					{:else}
						{#if result.out.cells.length > 1}
							<div class="flex flex-wrap items-center gap-1.5" role="tablist" aria-label="Cells">
								{#each result.out.cells as c, i (c.cell_key)}
									<button
										type="button"
										role="tab"
										aria-selected={i === selected}
										class={cn(
											'rounded-md border px-2 py-1 font-mono text-xs transition-colors duration-150',
											i === selected ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground hover:text-foreground',
										)}
										onclick={() => (selected = i)}
									>
										cell {i + 1} · {shortHash(c.cell_key)}
									</button>
								{/each}
								{#if result.out.truncated}<span class="text-xs text-muted-foreground">first {result.out.cells.length} of {plan?.n_cells}</span>{/if}
							</div>
						{/if}

						{#if cell}
							<section class={cn('rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]', stale && 'opacity-60')} aria-label="Cell detail">
								<CellDetailView studyId="" planHash={plan?.plan_hash ?? ''} cellKey={cell.cell_key} preloaded={cell} />
							</section>

							<section class="grid gap-4 lg:grid-cols-2">
								<div class="space-y-2 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]">
									<h2 class="section-label">Starter files ({starterTree.length})</h2>
									{#if starterTree.length}
										<ul class="max-h-72 overflow-y-auto font-mono text-xs leading-5">
											{#each starterTree as path (path)}<li class="truncate" title={path}>{path}</li>{/each}
										</ul>
									{:else}
										<p class="text-xs text-muted-foreground">No starter: the model starts from an empty workspace.</p>
									{/if}
								</div>
								<div class="space-y-2 rounded-md border border-border bg-card p-4 shadow-[var(--shadow-card)]">
									<div class="flex items-center justify-between gap-2">
										<h2 class="section-label">Run manifest</h2>
										<div class="flex gap-1">
											<Button variant="ghost" size="sm" onclick={() => copy('design', JSON.stringify(design, null, 2))}>
												<Copy />{copied === 'design' ? 'Copied' : 'Design'}
											</Button>
											<Button variant="ghost" size="sm" onclick={() => copy('manifest', JSON.stringify(cell.manifest, null, 2))}>
												<Copy />{copied === 'manifest' ? 'Copied' : 'Manifest'}
											</Button>
										</div>
									</div>
									<pre class="max-h-72 overflow-auto rounded border border-border bg-muted/40 p-2 font-mono text-[11px] leading-4">{JSON.stringify(cell.manifest, null, 2)}</pre>
								</div>
							</section>
						{/if}
					{/if}
				{/if}
			</div>
		</div>
	{/if}
</div>
