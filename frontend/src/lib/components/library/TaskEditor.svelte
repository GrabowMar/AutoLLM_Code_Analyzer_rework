<script lang="ts">
	/**
	 * TaskEditor — the structured TaskSpec (schema 4) editor. Tabs: Overview (title, summary, category,
	 * frontend, features, surfaces, auth flow, seed data) · Entities · Endpoints · UI · Acceptance · Probes ·
	 * Prose (minimal / standard / exhaustive) · Preview (derived figures, local cross-reference checks and
	 * the rendered prose).
	 *
	 * The editor keeps a `TaskDraft` (JSON parts as text, see `task.ts`) and emits
	 * `onchange(content, errors)` after each user edit; `errors` maps paths to messages for text that does
	 * not parse (the caller blocks saving while it is non-empty). An outside change of `value` (another
	 * version loaded) resets the draft.
	 *
	 * Props: `value`, `onchange`, `readonly`, `derived` (server `TaskDerived`, shown in Preview), `issues`
	 * (server lint issues, shown in Preview).
	 */
	import { untrack } from 'svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Select from '$lib/components/ui/select';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { renderMarkdown } from '$lib/docs/markdown';
	import type { Issue, ProseVariant, TaskDerived, TaskSpecContent } from '$lib/api/v3/types';
	import { PROSE_VARIANTS } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';
	import EntitiesEditor from './task/EntitiesEditor.svelte';
	import EndpointsEditor from './task/EndpointsEditor.svelte';
	import JsonItemsEditor from './task/JsonItemsEditor.svelte';
	import { jsonEqual } from './policy';
	import {
		draftFromTask,
		localTaskIssues,
		newCheckText,
		newProbeText,
		taskFromDraft,
		type TaskDraft,
		type TaskErrors,
	} from './task';

	interface Props {
		value: TaskSpecContent;
		onchange: (content: TaskSpecContent, errors: TaskErrors) => void;
		readonly?: boolean;
		derived?: TaskDerived | null;
		issues?: Issue[];
	}

	let { value, onchange, readonly = false, derived: taskDerived = null, issues = [] }: Props = $props();

	let draft = $state<TaskDraft>(draftFromTask(untrack(() => value)));
	let lastEmitted: TaskSpecContent | null = null;
	let errors = $state<TaskErrors>({});

	// Outside change (version switch, reset): reload the draft.
	$effect.pre(() => {
		const next = value;
		untrack(() => {
			if (lastEmitted === null || !jsonEqual(next, lastEmitted)) {
				const current = taskFromDraft($state.snapshot(draft) as TaskDraft).content;
				if (!jsonEqual(next, current)) draft = draftFromTask(next);
				lastEmitted = next;
			}
		});
	});

	// Every draft edit: rebuild the content and emit it when it changed.
	$effect(() => {
		const snap = $state.snapshot(draft) as TaskDraft;
		const result = taskFromDraft(snap);
		untrack(() => {
			errors = result.errors;
			if (lastEmitted && jsonEqual(result.content, lastEmitted)) return;
			lastEmitted = result.content;
			onchange(result.content, result.errors);
		});
	});

	const content = $derived(taskFromDraft($state.snapshot(draft) as TaskDraft).content);
	const local = $derived(localTaskIssues(content));
	const endpointIds = $derived(draft.endpoints.map((e) => e.id).filter(Boolean));
	const entityNames = $derived(draft.entities.map((e) => e.name).filter(Boolean));
	const errorCount = $derived(Object.keys(errors).length);

	let tab = $state('overview');
	let variant = $state<ProseVariant>('standard');

	// prose preview
	let proseView = $state<'write' | 'preview'>('write');
	let proseHtml = $state('');
	$effect(() => {
		if (proseView !== 'preview' && tab !== 'preview') return;
		const text = draft.prose[variant];
		let live = true;
		renderMarkdown(text || '')
			.then((r) => live && (proseHtml = r.html))
			.catch(() => live && (proseHtml = ''));
		return () => {
			live = false;
		};
	});

	const endpointItems = $derived(endpointIds.map((id) => ({ value: id, label: id })));
	const tabErrors = (prefix: string) => Object.keys(errors).some((k) => k.startsWith(prefix));
</script>

<Tabs.Root bind:value={tab}>
	<Tabs.List>
		<Tabs.Trigger value="overview">Overview{tabErrors('title') || tabErrors('summary') || tabErrors('category') || tabErrors('seed_data') ? ' ·!' : ''}</Tabs.Trigger>
		<Tabs.Trigger value="entities">Entities <span class="font-mono text-[11px] text-muted-foreground">{draft.entities.length}</span></Tabs.Trigger>
		<Tabs.Trigger value="endpoints">Endpoints <span class="font-mono text-[11px] text-muted-foreground">{draft.endpoints.length}</span>{tabErrors('endpoints') ? ' ·!' : ''}</Tabs.Trigger>
		<Tabs.Trigger value="ui">UI <span class="font-mono text-[11px] text-muted-foreground">{draft.ui.length}</span></Tabs.Trigger>
		<Tabs.Trigger value="acceptance">Acceptance <span class="font-mono text-[11px] text-muted-foreground">{draft.acceptance.length}</span>{tabErrors('acceptance') ? ' ·!' : ''}</Tabs.Trigger>
		<Tabs.Trigger value="probes">Probes <span class="font-mono text-[11px] text-muted-foreground">{draft.probes.length}</span>{tabErrors('probes') ? ' ·!' : ''}</Tabs.Trigger>
		<Tabs.Trigger value="prose">Prose{tabErrors('prose') ? ' ·!' : ''}</Tabs.Trigger>
		<Tabs.Trigger value="preview">Preview</Tabs.Trigger>
	</Tabs.List>

	<Tabs.Content value="overview" class="space-y-4 pt-4">
		<div class="grid gap-4 md:grid-cols-2">
			<FormRow label="Title" for="task-title" error={errors.title}>
				<Input id="task-title" bind:value={draft.title} readonly={readonly} />
			</FormRow>
			<FormRow label="Category" for="task-category" help="Free category (crud, auth, files, …)." error={errors.category}>
				<Input id="task-category" bind:value={draft.category} class="font-mono" readonly={readonly} />
			</FormRow>
			<FormRow label="Summary" for="task-summary" help="One sentence; shown in pickers and the brief's task section." error={errors.summary} class="md:col-span-2">
				<Input id="task-summary" bind:value={draft.summary} readonly={readonly} />
			</FormRow>
			<FormRow label="Features" for="task-features" help="Closed vocabulary (tasks/vocab.yaml); the lint rejects unknown ones.">
				<ListInput id="task-features" value={draft.features} {readonly} onchange={(v) => (draft.features = v)} placeholder="crud, auth, pagination" />
			</FormRow>
			<FormRow label="Security surfaces" for="task-surfaces" help="CWE ids the task exposes (cwe-89, cwe-79, …); probes and detectors are selected by them.">
				<ListInput id="task-surfaces" value={draft.surfaces} {readonly} onchange={(v) => (draft.surfaces = v)} placeholder="cwe-89, cwe-639" />
			</FormRow>
			<label class="flex items-center gap-2 text-sm"><Checkbox bind:checked={draft.has_frontend} disabled={readonly} /> The app has a frontend</label>
		</div>

		<section class="space-y-3 rounded-md border border-border p-3" aria-labelledby="task-auth-flow">
			<div class="flex flex-wrap items-center justify-between gap-2">
				<h3 id="task-auth-flow" class="section-label">Auth flow</h3>
				<label class="flex items-center gap-2 text-xs">
					<Checkbox bind:checked={draft.auth_flow.enabled} disabled={readonly} /> Defined (needed by the spec_defined auth mode)
				</label>
			</div>
			{#if draft.auth_flow.enabled}
				<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
					{#each [['register', 'Register endpoint'], ['login', 'Login endpoint'], ['logout', 'Logout endpoint'], ['csrf_endpoint', 'CSRF endpoint']] as const as [key, label] (key)}
						<FormRow {label} error={errors[`auth_flow.${key}`]}>
							<Select.Simple
								value={draft.auth_flow[key]}
								items={endpointItems}
								clearLabel={key === 'login' ? undefined : 'None'}
								placeholder="None"
								size="sm"
								disabled={readonly}
								onValueChange={(v) => (draft.auth_flow[key] = v)}
							/>
						</FormRow>
					{/each}
					<FormRow label="Credential">
						<Segmented aria-label="Credential" size="sm" value={draft.auth_flow.credential} disabled={readonly} items={[{ value: 'bearer', label: 'Bearer' }, { value: 'cookie', label: 'Cookie' }]} onValueChange={(v) => (draft.auth_flow.credential = v as 'bearer' | 'cookie')} />
					</FormRow>
					<FormRow label="Token JSON path" help="Where the login response holds the token.">
						<Input bind:value={draft.auth_flow.token_json_path} placeholder="$.token" class="h-8 font-mono" readonly={readonly} />
					</FormRow>
					<FormRow label="CSRF">
						<Select.Simple
							value={draft.auth_flow.csrf}
							items={[{ value: 'none', label: 'none' }, { value: 'double_submit_cookie', label: 'double-submit cookie' }, { value: 'header_from_endpoint', label: 'header from endpoint' }]}
							size="sm"
							disabled={readonly}
							onValueChange={(v) => (draft.auth_flow.csrf = v as typeof draft.auth_flow.csrf)}
						/>
					</FormRow>
				</div>
			{/if}
		</section>

		<FormRow label="Seed data" help={'Rows the app must expose on start (rare): {"Entity": [{…}, …]} as JSON.'} error={errors.seed_data}>
			<textarea
				bind:value={draft.seed_data_text}
				readonly={readonly}
				rows={4}
				spellcheck="false"
				placeholder={'{}'}
				class="block w-full rounded-md border border-input bg-surface-1 px-2 py-1.5 font-mono text-xs read-only:bg-muted/30"
			></textarea>
		</FormRow>
	</Tabs.Content>

	<Tabs.Content value="entities" class="pt-4">
		<EntitiesEditor bind:entities={draft.entities} {readonly} />
	</Tabs.Content>

	<Tabs.Content value="endpoints" class="pt-4">
		<EndpointsEditor bind:endpoints={draft.endpoints} {entityNames} {errors} {readonly} />
	</Tabs.Content>

	<Tabs.Content value="ui" class="space-y-2 pt-4">
		{#if draft.ui.length === 0}<p class="text-sm text-muted-foreground">No UI requirements.</p>{/if}
		{#each draft.ui as req, i (i)}
			<div class="grid gap-2 rounded-md border border-border p-2 sm:grid-cols-[10rem_minmax(0,1fr)_minmax(0,14rem)_auto]">
				<Input bind:value={req.id} placeholder="ui.list" class="h-8 font-mono" aria-label="Requirement id" readonly={readonly} />
				<Input bind:value={req.text} placeholder="What the UI must let the user do" class="h-8" aria-label="Requirement" readonly={readonly} />
				<ListInput value={req.endpoints} {readonly} placeholder="endpoint ids" aria-label="Endpoints" onchange={(v) => (req.endpoints = v)} />
				{#if !readonly}
					<Button variant="ghost" size="icon" aria-label="Remove requirement" onclick={() => (draft.ui = draft.ui.filter((_, j) => j !== i))}><Trash2 /></Button>
				{/if}
			</div>
		{/each}
		{#if !readonly}
			<Button variant="outline" size="sm" onclick={() => (draft.ui = [...draft.ui, { id: '', text: '', endpoints: [] }])}><Plus />Add UI requirement</Button>
		{/if}
	</Tabs.Content>

	<Tabs.Content value="acceptance" class="space-y-2 pt-4">
		<p class="text-xs text-muted-foreground">
			Hand-written extra checks (section 8.2). The compiler derives the standard checks from the endpoints; add only what it cannot (sort order, side effects, statedness via <span class="font-mono">stated_in</span>).
		</p>
		<JsonItemsEditor
			bind:items={draft.acceptance}
			path="acceptance"
			noun="check"
			newItem={() => newCheckText(endpointIds[0] ?? '')}
			summarize={(c) => `${c.id ?? '?'} · ${c.category ?? '?'}${c.endpoint ? ` · ${c.endpoint}` : ''}`}
			{errors}
			{readonly}
			empty="No hand-written checks: the compiled checks cover the endpoints."
		/>
	</Tabs.Content>

	<Tabs.Content value="probes" class="space-y-2 pt-4">
		<p class="text-xs text-muted-foreground">CWE probes (section 8.3) run with their preconditions and positive controls; they are reported apart from pass rates.</p>
		<JsonItemsEditor
			bind:items={draft.probes}
			path="probes"
			noun="probe"
			newItem={() => newProbeText(endpointIds[0] ?? '')}
			summarize={(p) => `${p.id ?? '?'} · ${p.kind ?? '?'} · ${p.cwe ?? '?'}${p.endpoint ? ` · ${p.endpoint}` : ''}`}
			{errors}
			{readonly}
		/>
	</Tabs.Content>

	<Tabs.Content value="prose" class="space-y-3 pt-4">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<Segmented aria-label="Prose variant" value={variant} items={PROSE_VARIANTS.map((v) => ({ value: v, label: v, badge: draft.prose[v].trim() ? undefined : 'empty' }))} onValueChange={(v) => (variant = v as ProseVariant)} />
			<Segmented aria-label="Write or preview" size="sm" value={proseView} items={[{ value: 'write', label: 'Write' }, { value: 'preview', label: 'Preview' }]} onValueChange={(v) => (proseView = v === 'preview' ? 'preview' : 'write')} />
		</div>
		<p class="text-xs text-muted-foreground">
			The prose variant is an experiment factor. Standard and exhaustive name every endpoint as <span class="font-mono">METHOD /path</span>; system tasks need all three variants. {errors.prose ?? ''}
		</p>
		{#if proseView === 'preview'}
			<div class="docs-article max-w-none rounded-md border border-border bg-surface-1 px-4 py-3 text-sm">{@html proseHtml}</div>
		{:else}
			<textarea
				bind:value={draft.prose[variant]}
				readonly={readonly}
				rows={22}
				aria-label="{variant} prose"
				class="block w-full rounded-md border border-input bg-surface-1 px-3 py-2 font-mono text-xs leading-relaxed read-only:bg-muted/30"
			></textarea>
		{/if}
	</Tabs.Content>

	<Tabs.Content value="preview" class="space-y-4 pt-4">
		{#if taskDerived}
			<dl class="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-7">
				{#each [['Size', taskDerived.size_bucket], ['Difficulty', taskDerived.difficulty_score.toFixed(2)], ['Endpoints', taskDerived.endpoint_count], ['Entities', taskDerived.entity_count], ['Fields', taskDerived.field_count], ['Relations', taskDerived.relation_count], ['Stated checks', Object.keys(taskDerived.check_statedness).length]] as [label, val] (label)}
					<div class="kpi-card">
						<dt class="kpi-label">{label}</dt>
						<dd class="kpi-value text-lg">{val}</dd>
					</div>
				{/each}
			</dl>
			<p class="text-[11px] text-muted-foreground">Derived figures are for the saved version (computed by the server, never hashed).</p>
		{/if}
		<div class="space-y-1">
			<p class="section-label">Checks</p>
			{#if errorCount === 0 && local.length === 0 && issues.length === 0}
				<p class="text-sm text-muted-foreground">No problems found in the draft. The server lint runs again on save.</p>
			{/if}
			{#each Object.entries(errors) as [path, message] (path)}
				<p class="text-xs text-danger-fg"><span class="font-mono">{path}</span> {message}</p>
			{/each}
			{#each local as issue, i (i)}
				<p class="text-xs text-warning-fg"><span class="font-mono">{issue.path}</span> {issue.message}</p>
			{/each}
			{#each issues as issue, i (i)}
				<p class="text-xs {issue.severity === 'error' ? 'text-danger-fg' : 'text-muted-foreground'}"><span class="font-mono">{issue.code}</span> {issue.message}</p>
			{/each}
		</div>
		<div class="space-y-1">
			<p class="section-label">Endpoints</p>
			<div class="overflow-x-auto rounded-md border border-border">
				<table class="w-full text-xs">
					<thead><tr><th>Id</th><th>Method</th><th>Path</th><th>Auth</th><th class="num">Status</th><th>Errors</th></tr></thead>
					<tbody>
						{#each content.endpoints as e (e.id + e.method + e.path)}
							<tr><td class="font-mono">{e.id}</td><td class="font-mono">{e.method}</td><td class="font-mono">{e.path}</td><td>{e.auth}{e.owner_entity ? ` (${e.owner_entity})` : ''}</td><td class="num">{e.success_status}</td><td class="font-mono">{e.errors.join(', ')}</td></tr>
						{/each}
					</tbody>
				</table>
			</div>
		</div>
		<div class="space-y-1">
			<p class="section-label">Prose · {variant}</p>
			<div class="docs-article max-w-none rounded-md border border-border bg-surface-1 px-4 py-3 text-sm">{@html proseHtml}</div>
		</div>
	</Tabs.Content>
</Tabs.Root>
