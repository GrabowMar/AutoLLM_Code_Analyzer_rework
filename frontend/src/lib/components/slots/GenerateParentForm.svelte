<script lang="ts">
	/**
	 * GenerateParentForm — "Generate a parent app" (spec 10.5 step 0): a compact dialog (task, stack, style,
	 * auth, model, harness preset, budget preset; one repeat, frugal by default) that resolves and launches a
	 * one-cell app study through `POST /studies/adhoc/`, follows the run on `run:<id>` (with a polling
	 * fallback) and, once it finishes as a tested app, opens `/slots/new?parent=<run>`. A run that ends any
	 * other way is reported with its outcome and a link; nothing is retried behind the researcher's back.
	 *
	 * Cost: under the budget preset's `require_confirm_above_usd` the run launches directly (the preset's own
	 * rule). Above it the server answers 400 `confirmation_required` (nothing is created: the call is atomic);
	 * the dialog then shows the p90 and expected cost and the threshold, and launches only on "Launch for ≤ $X",
	 * which re-sends the same design (same seed) with `confirm_usd = p90`. Editing the form drops the quote.
	 *
	 * Props: `open` (bindable), `task` (initial task ref).
	 */
	import { goto } from '$app/navigation';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import NumberInput from './NumberInput.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import { formatApiError } from '$lib/api/core';
	import { generateParentApp } from '$lib/api/v3/slots';
	import { getSample, subscribeRun } from '$lib/api/v3/samples';
	import type { Issue, RunOut } from '$lib/api/v3/types';
	import { canBeParent, isActiveStatus, OUTCOME_TONES, outcomeLabel, fmtUsd } from '$lib/components/runs/format';
	import { libraryOptions, modelOptions, type OptionItem } from './options';
	import {
		buildParentDesign,
		emptyParentAppForm,
		launchQuote,
		PARENT_DEFAULTS,
		parentStudyName,
		validateParentAppForm,
		type LaunchQuote,
		type ParentAppForm,
	} from './parent-design';
	import { randomSeed } from './selection';

	interface Props {
		open?: boolean;
		task?: string;
	}

	let { open = $bindable(false), task = '' }: Props = $props();

	let form = $state<ParentAppForm>(emptyParentAppForm());
	let touched = $state(false);
	let options = $state<Record<string, OptionItem[]>>({});
	let optionsError = $state('');
	let submitting = $state(false);
	let submitError = $state('');
	let issues = $state<Issue[]>([]);
	let runId = $state<string | null>(null);
	let run = $state<RunOut | null>(null);
	let estimate = $state<Record<string, unknown> | null>(null);
	/** A launch the budget preset wants confirmed: the exact request that produced the quote, and the quote. */
	let pending = $state<{ design: Record<string, unknown>; name: string; formKey: string; quote: LaunchQuote } | null>(null);
	const formKey = $derived(JSON.stringify(form));
	// The quote prices the form as it was: any edit retires it (the next Generate asks again).
	const quote = $derived(pending && pending.formKey === formKey ? pending.quote : null);
	let stopFollowing: (() => void) | null = null;
	let pollTimer: ReturnType<typeof setTimeout> | null = null;

	$effect(() => {
		if (!open) return;
		if (task && !form.task) form = { ...form, task };
		if (Object.keys(options).length) return;
		Promise.all([
			libraryOptions('task', true),
			libraryOptions('stack'),
			libraryOptions('style'),
			libraryOptions('harness_preset'),
			libraryOptions('resource_policy'),
			libraryOptions('eval_suite', true),
			modelOptions(true),
		])
			.then(([tasks, stacks, styles, harnesses, policies, suites, models]) => {
				options = { tasks, stacks, styles, harnesses, policies, suites, models };
				if (!form.eval_suite && suites.length) form = { ...form, eval_suite: suites[0].value };
			})
			.catch((e) => (optionsError = formatApiError(e, 'Could not load the library.')));
	});

	$effect(() => {
		if (!open) stopFollow();
	});

	const errors = $derived(validateParentAppForm(form));
	const following = $derived(runId !== null);

	function set<K extends keyof ParentAppForm>(key: K, value: ParentAppForm[K]) {
		form = { ...form, [key]: value };
	}

	function stopFollow() {
		stopFollowing?.();
		stopFollowing = null;
		if (pollTimer) clearTimeout(pollTimer);
		pollTimer = null;
	}

	async function poll(id: string) {
		try {
			run = await getSample(id);
		} catch {
			// keep the last state; the stream or the next poll will catch up
		}
		const settled = run && !isActiveStatus(run.status) && !['pending', 'running'].includes(run.latest_evaluation?.status ?? '');
		if (settled && run) {
			stopFollow();
			if (canBeParent(run)) await goto(`/slots/new?parent=${encodeURIComponent(run.id)}`);
			return;
		}
		pollTimer = setTimeout(() => void poll(id), 10_000);
	}

	function follow(id: string) {
		stopFollow();
		runId = id;
		stopFollowing = subscribeRun(id, (event) => {
			if (event.type === 'status' || event.type === 'evaluation') {
				if (pollTimer) clearTimeout(pollTimer);
				void poll(id);
			}
		});
		void poll(id);
	}

	async function submit(confirmUsd: number | null = null) {
		touched = true;
		if (Object.keys(errors).length) return;
		// Confirming re-sends the exact design that was priced (same seed); a fresh Generate draws a new one.
		const confirming = confirmUsd !== null && quote !== null && pending !== null;
		const design = confirming && pending ? pending.design : buildParentDesign(form, randomSeed());
		const name = confirming && pending ? pending.name : parentStudyName(form);
		const key = formKey;
		submitting = true;
		submitError = '';
		issues = [];
		if (!confirming) pending = null;
		try {
			const out = await generateParentApp({
				design,
				name,
				launch: true,
				...(confirming ? { confirm_usd: confirmUsd } : {}),
			});
			pending = null;
			issues = out.issues.filter((i) => i.severity !== 'info');
			estimate = out.estimate;
			if (!out.run_ids.length) {
				submitError = 'The experiment was resolved but launched no run; see the issues.';
				return;
			}
			follow(out.run_ids[0]);
		} catch (e) {
			const q = launchQuote(e);
			if (q) {
				// Not launched: show the cost and wait for an explicit confirmation.
				pending = { design, name, formKey: key, quote: q };
				return;
			}
			pending = null;
			submitError = formatApiError(e, 'Could not launch the parent app.');
			const body = e as { issues?: Issue[] };
			if (Array.isArray(body?.issues)) issues = body.issues;
		} finally {
			submitting = false;
		}
	}

	function reset() {
		stopFollow();
		runId = null;
		run = null;
		estimate = null;
		pending = null;
		issues = [];
		submitError = '';
	}

	// The one-cell plan's expected USD in its cap's unit (`Estimate.cap_basis`: list price on a subscription or
	// local endpoint, which bills $0)
	const expected = $derived.by(() => {
		const key = estimate?.cap_basis === 'list' ? 'expected_list_total' : 'expected_total';
		const value = estimate?.[key];
		return typeof value === 'number' ? value : null;
	});
</script>

{#snippet field(id: string, label: string, key: keyof ParentAppForm, items: OptionItem[], placeholder: string, clear?: string)}
	<div class="space-y-1">
		<Label for={id}>{label}</Label>
		<Select.Simple
			{id}
			value={String(form[key] ?? '')}
			onValueChange={(v) => set(key, v as never)}
			{items}
			{placeholder}
			clearLabel={clear}
			aria-invalid={touched && !!errors[key]}
		/>
		{#if touched && errors[key]}<p class="text-[11px] text-danger-fg">{errors[key]}</p>{/if}
	</div>
{/snippet}

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>Generate a parent app</Dialog.Title>
			<Dialog.Description>
				One agentic run of one task (an ad-hoc exploratory experiment, one repeat), evaluated at the chosen level. When
				it finishes tested you land on the slot picker with it as the parent.
			</Dialog.Description>
		</Dialog.Header>

		{#if optionsError}<ErrorAlert message={optionsError} />{/if}

		{#if !following}
			<form
				class="grid gap-3 sm:grid-cols-2"
				onsubmit={(e) => {
					e.preventDefault();
					void submit();
				}}
			>
				{@render field('gp-task', 'Task', 'task', options.tasks ?? [], 'Pick a task')}
				{@render field('gp-stack', 'Stack', 'stack', options.stacks ?? [], 'Pick a stack')}
				{@render field('gp-model', 'Model (tool calling)', 'model', options.models ?? [], 'Pick a model')}
				{@render field('gp-harness', 'Harness preset', 'harness_preset', options.harnesses ?? [], `default: ${PARENT_DEFAULTS.harness_preset}`, 'Default')}
				{@render field('gp-style', 'Style', 'style', options.styles ?? [], `default: ${PARENT_DEFAULTS.style}`, 'Default')}
				{@render field(
					'gp-auth',
					'Auth mode',
					'auth_mode',
					[
						{ value: 'contract_bearer', label: 'Contract · bearer token' },
						{ value: 'cookie_session', label: 'Contract · cookie session' },
						{ value: 'spec_defined', label: 'As the task specifies' },
					],
					`default: ${PARENT_DEFAULTS.auth_mode}`,
					'Default',
				)}
				{@render field('gp-policy', 'Budget preset', 'resource_policy', options.policies ?? [], 'frugal')}
				{@render field('gp-suite', 'Evaluation suite', 'eval_suite', options.suites ?? [], 'Pick a suite')}
				<div class="space-y-1">
					<Label for="gp-level">Evaluation level</Label>
					<Select.Simple
						id="gp-level"
						value={form.eval_level}
						onValueChange={(v) => set('eval_level', v as ParentAppForm['eval_level'])}
						items={[
							{ value: 'build', label: 'Build', description: 'Builds and starts it; no tests (cannot become a tested parent)' },
							{ value: 'functional', label: 'Functional', description: 'Functional checks' },
							{ value: 'security', label: 'Security (default)', description: 'Checks, probes, adherence, default SAST' },
							{ value: 'full', label: 'Full', description: 'Everything incl. dynamic analysis' },
						]}
					/>
				</div>
				<div class="space-y-1">
					<Label for="gp-seed">Seed</Label>
<NumberInput id="gp-seed" min={0} step={1} placeholder="random" value={form.base_seed} onvaluechange={(v) => set('base_seed', v)} />
				</div>
				{#if submitError}<div class="sm:col-span-2"><ErrorAlert message={submitError} /></div>{/if}
				{#if quote}
					<div class="space-y-1 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs sm:col-span-2" role="status">
						<p class="font-medium">Confirm the cost before launching</p>
						<p>
							Estimated <b class="font-mono tabular-nums">{fmtUsd(quote.expected_total)}</b> expected, up to
							<b class="font-mono tabular-nums">{fmtUsd(quote.p90_total)}</b> (p90), above the budget preset's confirmation
							threshold of <span class="font-mono tabular-nums">{fmtUsd(quote.threshold)}</span>. Nothing has been launched yet.
						</p>
					</div>
				{/if}
				{#if issues.length}
					<ul class="space-y-1 text-xs sm:col-span-2">
						{#each issues as issue, i (i)}
							<li><StatusBadge status={issue.severity} tone={issue.severity === 'error' ? 'danger' : 'warning'} label={issue.code} class="normal-case" /> {issue.message}</li>
						{/each}
					</ul>
				{/if}
				<Dialog.Footer class="sm:col-span-2">
					<Button variant="outline" size="sm" type="button" onclick={() => (open = false)}>Cancel</Button>
					{#if quote}
						{@const q = quote}
						<Button size="sm" type="button" disabled={submitting} onclick={() => void submit(q.p90_total)}>
							{#if submitting}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Sparkles class="mr-1.5 h-3.5 w-3.5" />{/if}
							Launch for ≤ {fmtUsd(q.p90_total)}
						</Button>
					{:else}
						<Button size="sm" type="submit" disabled={submitting}>
							{#if submitting}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Sparkles class="mr-1.5 h-3.5 w-3.5" />{/if}
							Generate (1 run)
						</Button>
					{/if}
				</Dialog.Footer>
			</form>
		{:else}
			<div class="space-y-3 text-sm" aria-live="polite">
				<p>
					Run <a class="font-mono text-primary hover:underline" href={`/samples/${runId}`} target="_blank" rel="noopener">{runId?.slice(0, 8)}</a>
					{#if expected !== null}· estimated {fmtUsd(expected)}{/if}
				</p>
				{#if run}
					<div class="flex flex-wrap items-center gap-2">
						<StatusBadge status={run.status} />
						{#if run.phase}<span class="font-mono text-xs text-muted-foreground">{run.phase}</span>{/if}
						{#if run.outcome}<StatusBadge status={run.outcome} tones={OUTCOME_TONES} label={outcomeLabel(run.outcome)} />{/if}
						{#if run.latest_evaluation}<span class="text-xs text-muted-foreground">evaluation {run.latest_evaluation.status}</span>{/if}
					</div>
					{#if !isActiveStatus(run.status) && !canBeParent(run) && !['pending', 'running'].includes(run.latest_evaluation?.status ?? '')}
						<ErrorAlert
							title="Not a usable parent"
							message={`The run finished as ${outcomeLabel(run.outcome)}; only tested apps can be parents. Open the sample to see why, or generate another.`}
						/>
					{:else}
						<p class="text-muted-foreground">
							<LoaderCircle class="mr-1 inline h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
							Generating and evaluating; this page follows the run and opens the slot picker when it is ready. You can close the dialog: the run continues.
						</p>
					{/if}
				{/if}
				<Dialog.Footer>
					<Button variant="outline" size="sm" onclick={reset}>Generate another</Button>
					<Button size="sm" href={`/samples/${runId}`}>Open the sample</Button>
				</Dialog.Footer>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
