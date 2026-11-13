<script lang="ts">
	/**
	 * Generate app — one agentic app from one choice per factor (task, stack, style, auth, prose, model,
	 * harness, Budget preset; advanced: prompt set, evaluation level, seed). The form is the design the
	 * experiment builder would produce for those choices (`$lib/components/generate/quick-design`).
	 *
	 * Layout: a configurator. Numbered steps on the left use the library's own data (task cards with
	 * descriptions and tags, a backend → frontend → database stack picker, harness × native/controlled,
	 * styles that fit the frontend, Budget preset cards); a sticky summary on the right shows the recipe,
	 * the live estimate, the plan's issues and the Generate button.
	 *
	 * Pricing: every complete form is sent to `POST /studies/adhoc/` with `quick` and without `launch` (a dry
	 * run: resolved and priced, nothing is created), debounced and aborted when superseded; the estimate and
	 * the plan's issues are shown, API errors inline under their step. "Generate" re-sends the same design
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
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import NumberInput from '$lib/components/slots/NumberInput.svelte';
	import ModelPicker from '$lib/components/studies/shared/ModelPicker.svelte';
	import { loadModelCatalog } from '$lib/components/studies/shared/catalog';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Search from '@lucide/svelte/icons/search';
	import Check from '@lucide/svelte/icons/check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { createAdhocStudy } from '$lib/api/v3/studies';
	import { EVAL_LEVELS, type AdhocOut, type Estimate, type Issue, type LibraryItemOut } from '$lib/api/v3/types';
	import type { LLMModelSummary } from '$lib/api/client';
	import { libraryOptions, loadLibrary, type OptionItem } from '$lib/components/slots/options';
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

	// `?model=` (a model id or a catalog slug, from model pages) and `?task=` pre-fill.
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

	// ── Library data ─────────────────────────────────────────────────────────────
	interface Lib {
		tasks: LibraryItemOut[];
		stacks: LibraryItemOut[];
		styles: LibraryItemOut[];
		harnesses: LibraryItemOut[];
		policies: LibraryItemOut[];
		promptSets: OptionItem[];
		models: LLMModelSummary[];
	}
	let lib = $state<Lib | null>(null);
	let optionsError = $state('');

	async function loadOptions() {
		optionsError = '';
		try {
			const [tasks, stacks, styles, harnesses, policies, promptSets, models] = await Promise.all([
				loadLibrary('task'),
				loadLibrary('stack'),
				loadLibrary('style'),
				loadLibrary('harness_preset'),
				loadLibrary('resource_policy'),
				libraryOptions('prompt_set'),
				loadModelCatalog(),
			]);
			const byName = (a: LibraryItemOut, b: LibraryItemOut) => (a.name || a.slug).localeCompare(b.name || b.slug);
			lib = {
				tasks: [...tasks].sort(byName),
				stacks: [...stacks].sort(byName),
				styles: [...styles].sort(byName),
				harnesses: [...harnesses].sort(byName),
				policies: [...policies].sort(byName),
				promptSets,
				models,
			};
			// A catalog slug from a model page becomes the model id the form needs.
			const m = untrack(() => form.model);
			if (m && !models.some((x) => x.model_id === m)) {
				const hit = models.find((x) => x.canonical_slug === m);
				if (hit) form = { ...untrack(() => form), model: hit.model_id };
			}
		} catch (e) {
			optionsError = formatApiError(e, 'Could not load the library and the models.');
		}
	}

	$effect(() => {
		untrack(() => void loadOptions());
	});

	// ── Pricing state ────────────────────────────────────────────────────────────
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
		if (!complete || !lib) return;
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

	// ── Step 1: task ────────────────────────────────────────────────────────────
	const taskRef = (t: LibraryItemOut) => `${t.slug}@${t.version}`;
	let taskQuery = $state('');
	let taskTag = $state('');
	// Tags worth filtering on: the domains, not the CWE ids every task carries.
	const taskTags = $derived.by(() => {
		const counts = new Map<string, number>();
		for (const t of lib?.tasks ?? [])
			for (const tag of t.tags) if (!tag.startsWith('cwe-') && tag !== 'auth') counts.set(tag, (counts.get(tag) ?? 0) + 1);
		return [...counts].filter(([, n]) => n >= 2).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([t]) => t);
	});
	const shownTasks = $derived.by(() => {
		const q = taskQuery.trim().toLowerCase();
		return (lib?.tasks ?? []).filter(
			(t) =>
				(!taskTag || t.tags.includes(taskTag)) &&
				(!q || `${t.name} ${t.slug} ${t.description}`.toLowerCase().includes(q)),
		);
	});
	const selectedTask = $derived(lib?.tasks.find((t) => taskRef(t) === form.task) ?? null);
	const cweCount = (t: LibraryItemOut) => t.tags.filter((g) => g.startsWith('cwe-')).length;

	// ── Step 2: stack (backend → frontend → database) ─────────────────────────────
	const col = (s: LibraryItemOut, k: string) => String(s.columns?.[k] ?? '');
	const selectedStack = $derived(lib?.stacks.find((s) => s.slug === form.stack) ?? null);
	let backend = $state('');
	let frontend = $state('');
	let database = $state('');
	// Keep the three axes in step with the chosen stack (pre-fill, or a pick elsewhere).
	$effect(() => {
		const s = selectedStack;
		if (!s) return;
		untrack(() => {
			backend = col(s, 'backend_framework');
			frontend = col(s, 'frontend_framework');
			database = col(s, 'database');
		});
	});
	const uniq = (xs: string[]) => [...new Set(xs.filter(Boolean))].sort();
	/** Axis values: "free" means the stack leaves that choice to the model. */
	const axisLabel = (v: string) => (v === 'free' ? "model's choice" : v === 'none' ? 'server-rendered' : v);
	const backends = $derived(uniq((lib?.stacks ?? []).map((s) => col(s, 'backend_framework'))));
	const frontendsFor = $derived(uniq((lib?.stacks ?? []).filter((s) => col(s, 'backend_framework') === backend).map((s) => col(s, 'frontend_framework'))));
	const databasesFor = $derived(
		uniq(
			(lib?.stacks ?? [])
				.filter((s) => col(s, 'backend_framework') === backend && col(s, 'frontend_framework') === frontend)
				.map((s) => col(s, 'database')),
		),
	);
	const allFrontends = $derived(uniq((lib?.stacks ?? []).map((s) => col(s, 'frontend_framework'))));
	const allDatabases = $derived(uniq((lib?.stacks ?? []).map((s) => col(s, 'database'))));

	/** Pick the stack matching the axes; fall back to the first available frontend / database. */
	function pickStack(b: string, f: string, d: string) {
		const stacks = lib?.stacks ?? [];
		const fs = uniq(stacks.filter((s) => col(s, 'backend_framework') === b).map((s) => col(s, 'frontend_framework')));
		const fe = fs.includes(f) ? f : (fs.find((x) => x === 'react') ?? fs[0] ?? '');
		const ds = uniq(stacks.filter((s) => col(s, 'backend_framework') === b && col(s, 'frontend_framework') === fe).map((s) => col(s, 'database')));
		const db = ds.includes(d) ? d : (ds.find((x) => x === 'sqlite') ?? ds[0] ?? '');
		backend = b;
		frontend = fe;
		database = db;
		const hit = stacks.find(
			(s) => col(s, 'backend_framework') === b && col(s, 'frontend_framework') === fe && col(s, 'database') === db,
		);
		if (hit) set('stack', hit.slug);
	}

	// ── Step 3: model & harness ──────────────────────────────────────────────────
	let modelPk = $state<number | null>(null);
	$effect(() => {
		const id = form.model;
		const models = lib?.models;
		if (!models) return;
		untrack(() => {
			const pk = models.find((m) => m.model_id === id)?.id ?? null;
			if (pk !== modelPk) modelPk = pk;
		});
	});
	function onModelPicked() {
		const m = lib?.models.find((x) => x.id === modelPk);
		set('model', m?.model_id ?? '');
	}
	const selectedModel = $derived(lib?.models.find((m) => m.model_id === form.model) ?? null);

	const harnessName = (h: LibraryItemOut) => col(h, 'harness') || h.slug.replace(/-(native|controlled)$/, '');
	const harnessMode = (h: LibraryItemOut) => (h.slug.endsWith('-controlled') ? 'controlled' : 'native');
	const harnessNames = $derived(uniq((lib?.harnesses ?? []).map(harnessName)));
	const selectedHarness = $derived(lib?.harnesses.find((h) => h.slug === form.harness_preset) ?? null);
	const harnessModeNow = $derived(selectedHarness ? harnessMode(selectedHarness) : 'native');
	function pickHarness(name: string, mode: string) {
		const list = lib?.harnesses ?? [];
		const hit = list.find((h) => harnessName(h) === name && harnessMode(h) === mode) ?? list.find((h) => harnessName(h) === name);
		if (hit) set('harness_preset', hit.slug);
	}

	// ── Step 4: style & brief ────────────────────────────────────────────────────
	// Styles tag the frontends they fit; a stack without a frontend fits "none" styles.
	const shownStyles = $derived((lib?.styles ?? []).filter((s) => !frontend || s.tags.includes(frontend) || s.tags.includes('none')));
	const selectedStyle = $derived(lib?.styles.find((s) => s.slug === form.style) ?? null);

	// ── Step 5: budget ───────────────────────────────────────────────────────────
	const selectedPolicy = $derived(lib?.policies.find((p) => p.slug === form.resource_policy) ?? null);

	// ── Summary ──────────────────────────────────────────────────────────────────
	const recipe = $derived([
		{ step: 1, label: 'Task', value: selectedTask?.name ?? '', field: 'task' as Field },
		{ step: 2, label: 'Stack', value: selectedStack?.name ?? '', field: 'stack' as Field },
		{ step: 3, label: 'Model', value: selectedModel ? (selectedModel.model_name.split(': ').pop() ?? selectedModel.model_name) : '', field: 'model' as Field },
		{ step: 3, label: 'Harness', value: selectedHarness?.name ?? '', field: 'harness_preset' as Field },
		{ step: 4, label: 'Style', value: selectedStyle?.name ?? '', field: 'style' as Field },
		{ step: 4, label: 'Brief', value: `${AUTH_ITEMS.find((a) => a.value === form.auth_mode)?.label ?? '—'} · ${PROSE_ITEMS.find((p) => p.value === form.prose_variant)?.label ?? '—'}`, field: 'auth_mode' as Field },
		{ step: 5, label: 'Budget', value: selectedPolicy?.name ?? '', field: 'resource_policy' as Field },
	]);
	const missing = $derived(recipe.filter((r) => !r.value).length);
	const fieldErrors = $derived(Object.entries(apiErrors).filter(([, m]) => !!m) as [Field, string][]);

	function wallRange(lowS: number, highS: number): string {
		const lo = Math.max(1, Math.round(lowS / 60));
		const hi = Math.max(1, Math.round(highS / 60));
		return lo === hi ? `~${lo} min` : `${lo}–${hi} min`;
	}

	function scrollToStep(n: number) {
		document.getElementById(`step-${n}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}
</script>

{#snippet stepHead(n: number, title: string, done: boolean, aside?: string)}
	<header class="gs-head">
		<span class="gs-num" data-done={done}>{#if done}<Check class="size-3.5" />{:else}{n}{/if}</span>
		<h2 class="gs-title">{title}</h2>
		{#if aside}<span class="gs-aside">{aside}</span>{/if}
	</header>
{/snippet}

{#snippet fieldError(key: Field)}
	{#if errorFor(key)}<p class="gs-err"><CircleAlert class="size-3.5" />{errorFor(key)}</p>{/if}
{/snippet}

{#snippet chips(items: { value: string; label: string; title?: string; disabled?: boolean }[], value: string, onpick: (v: string) => void, label: string)}
	<div class="gs-chips" role="radiogroup" aria-label={label}>
		{#each items as item (item.value)}
			<button
				type="button"
				role="radio"
				aria-checked={value === item.value}
				class="gs-chip"
				data-on={value === item.value}
				disabled={item.disabled}
				title={item.title}
				onclick={() => onpick(item.value)}>{item.label}</button
			>
		{/each}
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

	{#if !lib && !optionsError}
		<LoadingState label="loading the library" />
	{:else if lib}
		<form
			class="gs-layout"
			onsubmit={(e) => {
				e.preventDefault();
				void generate(null);
			}}
		>
			<div class="gs-steps">
				<!-- 1 · Task -->
				<section id="step-1" class="gs-card">
					{@render stepHead(1, 'What to build', !!selectedTask, `${lib.tasks.length} tasks`)}
					<div class="gs-body">
						<div class="gs-taskbar">
							<div class="gs-search">
								<Search class="gs-search-icon" aria-hidden="true" />
								<input type="search" placeholder="Search tasks…" aria-label="Search tasks" bind:value={taskQuery} />
							</div>
							<div class="gs-chips gs-chips-sm" role="radiogroup" aria-label="Task area">
								<button type="button" role="radio" aria-checked={!taskTag} class="gs-chip" data-on={!taskTag} onclick={() => (taskTag = '')}>All</button>
								{#each taskTags as tag (tag)}
									<button type="button" role="radio" aria-checked={taskTag === tag} class="gs-chip" data-on={taskTag === tag} onclick={() => (taskTag = taskTag === tag ? '' : tag)}>{tag}</button>
								{/each}
							</div>
						</div>
						<div class="gs-tasks" role="radiogroup" aria-label="Task">
							{#each shownTasks as t (t.slug)}
								{@const on = taskRef(t) === form.task}
								<button type="button" role="radio" aria-checked={on} class="gs-task" data-on={on} onclick={() => set('task', taskRef(t))}>
									<span class="gs-task-name">{t.name || t.slug}{#if on}<Check class="ml-auto size-4 shrink-0 text-primary" />{/if}</span>
									<span class="gs-task-desc">{t.description}</span>
									<span class="gs-task-meta">
										{#each t.tags.filter((g) => !g.startsWith('cwe-')).slice(0, 3) as g (g)}<span>{g}</span>{/each}
										{#if cweCount(t)}<span class="gs-task-cwe" title="Security weaknesses the probes test">{cweCount(t)} CWE</span>{/if}
									</span>
								</button>
							{:else}
								<p class="col-span-full py-6 text-center text-sm text-muted-foreground">No task matches.</p>
							{/each}
						</div>
						{@render fieldError('task')}
					</div>
				</section>

				<!-- 2 · Stack -->
				<section id="step-2" class="gs-card">
					{@render stepHead(2, 'Stack', !!selectedStack, `${lib.stacks.length} stacks`)}
					<div class="gs-body space-y-3">
						<div class="gs-axis">
							<span class="gs-axis-label">Backend</span>
							{@render chips(backends.map((b) => ({ value: b, label: axisLabel(b) })), backend, (v) => pickStack(v, frontend, database), 'Backend')}
						</div>
						<div class="gs-axis">
							<span class="gs-axis-label">Frontend</span>
							{@render chips(
								allFrontends.map((f) => ({ value: f, label: axisLabel(f), disabled: !!backend && !frontendsFor.includes(f), title: backend && !frontendsFor.includes(f) ? `No ${backend} stack with ${f}` : undefined })),
								frontend,
								(v) => pickStack(backend, v, database),
								'Frontend',
							)}
						</div>
						<div class="gs-axis">
							<span class="gs-axis-label">Database</span>
							{@render chips(
								allDatabases.map((d) => ({ value: d, label: axisLabel(d), disabled: !!backend && !databasesFor.includes(d), title: backend && !databasesFor.includes(d) ? 'Not offered with this backend and frontend' : undefined })),
								database,
								(v) => pickStack(backend, frontend, v),
								'Database',
							)}
						</div>
						{#if selectedStack}
							<p class="gs-note"><b>{selectedStack.name}</b> — {selectedStack.description}</p>
						{:else if !backend}
							<p class="gs-note">Start with a backend; only combinations that exist in the library can be picked.</p>
						{/if}
						{@render fieldError('stack')}
					</div>
				</section>

				<!-- 3 · Model & harness -->
				<section id="step-3" class="gs-card">
					{@render stepHead(3, 'Who builds it', !!selectedModel && !!selectedHarness, 'models with tool calling')}
					<div class="gs-body space-y-4">
						<ModelPicker
							mode="single"
							bind:selectedId={modelPk}
							onchange={onModelPicked}
							requireTools
							groupBy="endpoint"
							label=""
							aria-label="Model"
							idPrefix="gen-model"
							maxHeight="max-h-72"
						/>
						{@render fieldError('model')}
						<div class="gs-axis">
							<span class="gs-axis-label">Harness</span>
							{@render chips(harnessNames.map((n) => ({ value: n, label: n })), selectedHarness ? harnessName(selectedHarness) : '', (v) => pickHarness(v, harnessModeNow), 'Harness')}
						</div>
						<div class="gs-axis">
							<span class="gs-axis-label">Mode</span>
							{@render chips(
								[
									{ value: 'native', label: 'Native', title: 'The harness’s own system prompt, tools and defaults' },
									{ value: 'controlled', label: 'Controlled', title: 'Equalised knobs across harnesses (spec 5.5)' },
								],
								harnessModeNow,
								(v) => selectedHarness && pickHarness(harnessName(selectedHarness), v),
								'Harness mode',
							)}
						</div>
						{#if selectedHarness}<p class="gs-note">{selectedHarness.description}</p>{/if}
						{@render fieldError('harness_preset')}
					</div>
				</section>

				<!-- 4 · Style & brief -->
				<section id="step-4" class="gs-card">
					{@render stepHead(4, 'Style & brief', !!selectedStyle, frontend ? `styles for ${frontend === 'none' ? 'server-rendered pages' : frontend}` : undefined)}
					<div class="gs-body space-y-4">
						<div class="gs-axis">
							<span class="gs-axis-label">Style</span>
							{@render chips(shownStyles.map((s) => ({ value: s.slug, label: s.name, title: s.description })), form.style, (v) => set('style', v), 'Style')}
						</div>
						{@render fieldError('style')}
						<div class="gs-axis">
							<span class="gs-axis-label">Auth</span>
							{@render chips(AUTH_ITEMS.map((a) => ({ value: a.value, label: a.label })), form.auth_mode, (v) => set('auth_mode', v), 'Auth mode')}
						</div>
						<div class="gs-axis">
							<span class="gs-axis-label">Brief</span>
							{@render chips(PROSE_ITEMS.map((p) => ({ value: p.value, label: p.label, title: p.description })), form.prose_variant, (v) => set('prose_variant', v), 'Prose variant')}
						</div>
						<p class="gs-note">{PROSE_ITEMS.find((p) => p.value === form.prose_variant)?.description ?? FACTOR_BY_KEY.prose_variant.help}</p>
					</div>
				</section>

				<!-- 5 · Budget & advanced -->
				<section id="step-5" class="gs-card">
					{@render stepHead(5, 'Budget', !!selectedPolicy, 'caps enforced outside the model')}
					<div class="gs-body space-y-4">
						<div class="gs-policies" role="radiogroup" aria-label="Budget preset">
							{#each lib.policies as p (p.slug)}
								{@const on = form.resource_policy === p.slug}
								<button type="button" role="radio" aria-checked={on} class="gs-policy" data-on={on} onclick={() => set('resource_policy', p.slug)}>
									<span class="gs-policy-name">{p.name}{#if on}<Check class="ml-auto size-4 shrink-0 text-primary" />{/if}</span>
									<span class="gs-policy-desc">{p.description}</span>
								</button>
							{/each}
						</div>
						{@render fieldError('resource_policy')}

						<div class="gs-advanced">
							<button type="button" class="gs-adv-toggle" aria-expanded={advancedOpen} onclick={() => (advancedOpen = !advancedOpen)}>
								Advanced <span class="text-muted-foreground">prompt set · evaluation level · seed</span>
								<ChevronDown class="ml-auto size-4 transition-transform {advancedOpen ? 'rotate-180' : ''}" />
							</button>
							{#if advancedOpen}
								<div class="grid gap-4 border-t border-border/60 px-3 py-3 sm:grid-cols-3">
									<div class="space-y-1">
										<Label for="gen-prompts">Prompt set</Label>
										<Select.Simple
											id="gen-prompts"
											value={form.prompt_set}
											onValueChange={(v) => set('prompt_set', v)}
											items={lib.promptSets}
											placeholder={`default: ${MODE_DEFAULTS.app.prompt_set}`}
											clearLabel="Default"
										/>
										{@render fieldError('prompt_set')}
									</div>
									<div class="space-y-1">
										<Label for="gen-level">Evaluation level</Label>
										<Select.Simple id="gen-level" value={form.eval_level} onValueChange={(v) => set('eval_level', v as never)} items={LEVEL_ITEMS} />
										{@render fieldError('eval_level')}
									</div>
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
										{@render fieldError('base_seed')}
									</div>
								</div>
							{/if}
						</div>
					</div>
				</section>
			</div>

			<!-- Sticky summary: the recipe, the live estimate and the one action. -->
			<aside class="gs-summary" aria-label="Summary">
				<div class="gs-card gs-sum-card">
					<h2 class="gs-sum-title">Your app</h2>
					<dl class="gs-recipe">
						{#each recipe as r (r.label)}
							<div>
								<dt>{r.label}</dt>
								<dd>
									{#if r.value}{r.value}{:else}<button type="button" class="gs-choose" onclick={() => scrollToStep(r.step)}>choose…</button>{/if}
								</dd>
							</div>
						{/each}
					</dl>

					<div class="gs-estimate" aria-live="polite">
						{#if !complete}
							<p class="text-xs text-muted-foreground">{missing ? `${missing} choice${missing === 1 ? '' : 's'} left` : 'Complete the choices'} to see the estimate.</p>
						{:else if pricing && !current}
							<div class="gs-est-grid" aria-busy="true">
								{#each [0, 1, 2] as i (i)}<div><span class="gs-skel h-2.5 w-12"></span><span class="gs-skel mt-1.5 h-5 w-16"></span></div>{/each}
							</div>
						{:else if priceError}
							<ErrorAlert message={priceError} />
						{:else if fieldErrors.length}
							<p class="text-xs text-danger-fg">The API refused a choice: see the highlighted step.</p>
						{:else if current && totals}
							<dl class="gs-est-grid">
								<div><dt>Expected</dt><dd>{formatUsd(totals.expected)}</dd></div>
								<div><dt>Up to (p90)</dt><dd>{formatUsd(totals.p90)}</dd></div>
								{#if estimate?.time}
									<div>
										<dt>Wall time</dt>
										<dd>{wallRange(estimate.time.expected_wall_s_low, estimate.time.expected_wall_s_high)}</dd>
									</div>
								{/if}
							</dl>
							{#if totals.basis === 'list'}<p class="mt-2 text-[11px] text-muted-foreground">At list price: this endpoint bills $0; the cap counts list price.</p>{/if}
							{#if pricing}<p class="mt-1 text-[11px] text-muted-foreground">Updating…</p>{/if}
						{/if}
					</div>

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

					{#if currentQuote}
						{@const q = currentQuote}
						<Button class="w-full" type="button" disabled={generating} onclick={() => void generate(q.p90_total)}>
							{#if generating}<LoaderCircle class="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" />{:else}<Sparkles class="mr-1.5 h-4 w-4" />{/if}
							Generate for ≤ {formatUsd(q.p90_total)}
						</Button>
					{:else}
						<Button class="w-full" type="submit" disabled={!canGenerate} title={blocking.length ? 'Fix the blocking issues first' : undefined}>
							{#if generating}<LoaderCircle class="mr-1.5 h-4 w-4 animate-spin motion-reduce:animate-none" />{:else}<Sparkles class="mr-1.5 h-4 w-4" />{/if}
							{totals && current ? `Generate (≤ ${formatUsd(totals.p90)})` : 'Generate'}
						</Button>
					{/if}
					<p class="text-center text-[11px] text-muted-foreground">Opens the run page, which follows the build live.</p>
				</div>
			</aside>
			<!-- Small screens: the summary sits below the steps, so the price and the action stay reachable here. -->
			<div class="gs-mobilebar">
				<div class="min-w-0 text-xs">
					{#if current && totals}
						<span class="font-mono text-sm font-medium">≤ {formatUsd(totals.p90)}</span>
						<span class="text-muted-foreground"> p90 · {formatUsd(totals.expected)} expected</span>
					{:else if !complete}
						<span class="text-muted-foreground">{missing} choice{missing === 1 ? '' : 's'} left</span>
					{:else}
						<span class="text-muted-foreground">Pricing…</span>
					{/if}
				</div>
				<Button size="sm" type="submit" disabled={!canGenerate || !!currentQuote}>
					<Sparkles class="mr-1.5 h-3.5 w-3.5" />Generate
				</Button>
			</div>
		</form>
	{/if}
</div>

<style>
	.gs-layout {
		display: grid;
		grid-template-columns: minmax(0, 1fr) 21rem;
		align-items: start;
		gap: 1rem;
	}
	.gs-steps {
		display: flex;
		min-width: 0;
		flex-direction: column;
		gap: 1rem;
	}
	.gs-card {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--card);
		box-shadow: var(--shadow-card);
		scroll-margin-top: 4rem;
	}
	.gs-head {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		padding: 0.875rem 1.25rem 0;
	}
	.gs-num {
		display: inline-flex;
		width: 1.625rem;
		height: 1.625rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 9999px;
		border: 1px solid var(--border);
		font-family: var(--font-mono);
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.gs-num[data-done='true'] {
		border-color: var(--primary);
		background: var(--primary);
		color: var(--primary-foreground);
	}
	.gs-title {
		font-family: var(--font-display);
		font-size: 1.0625rem;
		font-weight: 600;
	}
	.gs-aside {
		margin-left: auto;
		font-size: 0.75rem;
		color: var(--muted-foreground);
	}
	.gs-body {
		padding: 0.875rem 1.25rem 1.125rem;
	}
	.gs-note {
		font-size: 0.8125rem;
		color: var(--muted-foreground);
	}
	.gs-note b {
		font-weight: 600;
		color: var(--foreground);
	}
	.gs-err {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		margin-top: 0.5rem;
		font-size: 0.75rem;
		color: var(--danger-fg);
	}

	/* chips (radio groups) */
	.gs-chips {
		display: flex;
		flex-wrap: wrap;
		gap: 0.375rem;
	}
	.gs-chip {
		height: 2rem;
		cursor: pointer;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--border);
		background: var(--card);
		padding-inline: 0.75rem;
		font-size: 0.8125rem;
		transition: border-color 150ms, background-color 150ms, color 150ms;
	}
	.gs-chips-sm .gs-chip {
		height: 1.625rem;
		padding-inline: 0.5rem;
		font-size: 0.75rem;
	}
	.gs-chip:hover:not(:disabled) {
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
	}
	.gs-chip[data-on='true'] {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 12%, var(--card));
		color: var(--accent-foreground);
		font-weight: 500;
	}
	.gs-chip:disabled {
		cursor: not-allowed;
		opacity: 0.35;
	}
	.gs-chip:focus-visible,
	.gs-task:focus-visible,
	.gs-policy:focus-visible {
		outline: 2px solid var(--ring);
		outline-offset: 1px;
	}
	.gs-axis {
		display: grid;
		grid-template-columns: 5.5rem minmax(0, 1fr);
		align-items: start;
		gap: 0.75rem;
	}
	.gs-axis-label {
		padding-top: 0.45rem;
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.1em;
		color: var(--muted-foreground);
	}

	/* tasks */
	.gs-taskbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem 0.75rem;
		margin-bottom: 0.75rem;
	}
	.gs-search {
		position: relative;
		flex: 0 1 16rem;
	}
	.gs-search :global(.gs-search-icon) {
		position: absolute;
		left: 0.625rem;
		top: 50%;
		width: 0.875rem;
		height: 0.875rem;
		transform: translateY(-50%);
		color: var(--muted-foreground);
	}
	.gs-search input {
		height: 2rem;
		width: 100%;
		border-radius: calc(var(--radius) + 0.125rem);
		border: 1px solid var(--input);
		background: var(--surface-1, var(--card));
		padding: 0 0.75rem 0 2rem;
		font-size: 0.8125rem;
	}
	.gs-search input:focus-visible {
		outline: none;
		border-color: var(--ring);
	}
	.gs-tasks {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
		gap: 0.5rem;
		max-height: 24rem;
		overflow-y: auto;
		padding: 2px;
	}
	.gs-task,
	.gs-policy {
		display: flex;
		min-width: 0;
		cursor: pointer;
		flex-direction: column;
		gap: 0.375rem;
		border-radius: var(--radius);
		border: 1px solid var(--border);
		background: var(--card);
		padding: 0.625rem 0.75rem;
		text-align: left;
		transition: border-color 150ms, background-color 150ms;
	}
	.gs-task:hover,
	.gs-policy:hover {
		border-color: color-mix(in oklab, var(--primary) 45%, var(--border));
	}
	.gs-task[data-on='true'],
	.gs-policy[data-on='true'] {
		border-color: var(--primary);
		background: color-mix(in oklab, var(--primary) 8%, var(--card));
		box-shadow: 0 0 0 1px var(--primary);
	}
	.gs-task-name,
	.gs-policy-name {
		display: flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.875rem;
		font-weight: 600;
	}
	.gs-task-desc,
	.gs-policy-desc {
		display: -webkit-box;
		overflow: hidden;
		-webkit-line-clamp: 2;
		line-clamp: 2;
		-webkit-box-orient: vertical;
		font-size: 0.75rem;
		line-height: 1.4;
		color: var(--muted-foreground);
	}
	.gs-task-meta {
		display: flex;
		flex-wrap: wrap;
		gap: 0.25rem;
		margin-top: auto;
	}
	.gs-task-meta span {
		border-radius: 0.25rem;
		background: var(--muted);
		padding: 0 0.3125rem;
		font-family: var(--font-mono);
		font-size: 0.625rem;
		line-height: 1rem;
		color: var(--muted-foreground);
	}
	.gs-task-meta .gs-task-cwe {
		background: color-mix(in oklab, var(--warning) 14%, transparent);
		color: var(--warning-fg);
	}
	.gs-policies {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
		gap: 0.5rem;
	}
	.gs-advanced {
		border: 1px solid var(--border);
		border-radius: var(--radius);
	}
	.gs-adv-toggle {
		display: flex;
		width: 100%;
		cursor: pointer;
		align-items: center;
		gap: 0.5rem;
		padding: 0.5rem 0.75rem;
		font-size: 0.8125rem;
		font-weight: 500;
		text-align: left;
	}

	/* summary */
	.gs-summary {
		position: sticky;
		top: 4rem;
	}
	.gs-sum-card {
		display: flex;
		flex-direction: column;
		gap: 1rem;
		padding: 1rem 1.125rem 1.125rem;
	}
	.gs-sum-title {
		font-family: var(--font-display);
		font-size: 1.0625rem;
		font-weight: 600;
	}
	.gs-recipe {
		display: flex;
		flex-direction: column;
	}
	.gs-recipe > div {
		display: grid;
		grid-template-columns: 4.5rem minmax(0, 1fr);
		gap: 0.5rem;
		padding: 0.375rem 0;
		border-bottom: 1px dashed color-mix(in oklab, var(--border) 80%, transparent);
		font-size: 0.8125rem;
	}
	.gs-recipe dt {
		font-family: var(--font-mono);
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--muted-foreground);
		padding-top: 0.125rem;
	}
	.gs-recipe dd {
		min-width: 0;
		overflow-wrap: anywhere;
	}
	.gs-choose {
		cursor: pointer;
		font-style: italic;
		color: var(--primary);
	}
	.gs-estimate {
		border-radius: var(--radius);
		background: color-mix(in oklab, var(--primary) 6%, var(--muted));
		padding: 0.75rem 0.875rem;
	}
	.gs-est-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.5rem;
	}
	.gs-est-grid dt {
		font-family: var(--font-mono);
		font-size: 0.625rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--muted-foreground);
	}
	.gs-est-grid dd {
		font-family: var(--font-mono);
		font-size: 1.0625rem;
		font-weight: 500;
		font-variant-numeric: tabular-nums;
	}
	.gs-skel {
		display: block;
		border-radius: 0.25rem;
		background: color-mix(in oklab, var(--muted-foreground) 20%, transparent);
		animation: gs-pulse 1.4s ease-in-out infinite;
	}
	@keyframes gs-pulse {
		50% {
			opacity: 0.45;
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.gs-skel {
			animation: none;
		}
	}

	.gs-mobilebar {
		display: none;
	}
	@media (max-width: 1100px) {
		.gs-layout {
			grid-template-columns: minmax(0, 1fr);
			padding-bottom: 4rem;
		}
		.gs-summary {
			position: static;
		}
		.gs-mobilebar {
			position: fixed;
			inset: auto 0 0 0;
			z-index: 30;
			display: flex;
			align-items: center;
			justify-content: space-between;
			gap: 0.75rem;
			border-top: 1px solid var(--border);
			background: color-mix(in oklab, var(--card) 94%, transparent);
			backdrop-filter: blur(8px);
			padding: 0.625rem 1rem calc(0.625rem + env(safe-area-inset-bottom));
		}
	}
	@media (max-width: 640px) {
		.gs-axis {
			grid-template-columns: minmax(0, 1fr);
			gap: 0.375rem;
		}
		.gs-axis-label {
			padding-top: 0;
		}
		.gs-head,
		.gs-body {
			padding-inline: 1rem;
		}
	}
</style>
