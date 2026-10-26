<script lang="ts">
	import { untrack } from 'svelte';
	/**
	 * SpecEditor — the whole app spec, including the graded contract (data
	 * model, API and admin endpoints) the hidden conformance suite checks.
	 * Form and JSON views edit the same draft; JSON accepts a seeded spec file.
	 *
	 * Props: `initial` (null = new), `copy` (duplicate `initial` under a new
	 * slug), `readonly` (catalog spec for non-staff: view + Duplicate),
	 * `categories` (suggestions), `takenSlugs`, `saving`, `error`,
	 * `onSave(payload, isNew)`, `onDuplicate`, `onCancel`.
	 */
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Textarea } from '$lib/components/ui/textarea';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Save from '@lucide/svelte/icons/save';
	import Copy from '@lucide/svelte/icons/copy';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import XIcon from '@lucide/svelte/icons/x';
	import type { AppRequirementTemplate, AppSpecDifficulty } from '$lib/api/generation';
	import EndpointListEditor from './EndpointListEditor.svelte';
	import {
		DIFFICULTIES,
		draftToJson,
		draftToPayload,
		emptyField,
		jsonToDraft,
		specIssues,
		specSlugFromName,
		specToDraft,
		type SpecPayload,
	} from './spec';

	interface Props {
		initial: AppRequirementTemplate | null;
		copy?: boolean;
		readonly?: boolean;
		categories?: string[];
		takenSlugs?: string[];
		saving: boolean;
		error: string;
		onSave: (payload: SpecPayload, isNew: boolean) => void;
		onDuplicate?: () => void;
		onCancel: () => void;
	}
	let {
		initial,
		copy = false,
		readonly = false,
		categories = [],
		takenSlugs = [],
		saving,
		error,
		onSave,
		onDuplicate,
		onCancel,
	}: Props = $props();

	const isNew = $derived(initial === null || copy);
	// The editor is re-keyed per spec, so the props it starts from never change.
	let draft = $state(untrack(() => specToDraft(initial, copy)));
	let slugTouched = $state(false);
	// Show field problems only after the first save attempt (or once typed into).
	let attempted = $state(false);

	let view = $state<'form' | 'json'>('form');
	let jsonText = $state('');
	let jsonError = $state('');

	const issues = $derived(specIssues(draft, { isNew, takenSlugs }));
	const errors = $derived(issues.filter((i) => i.level === 'error'));
	const warnings = $derived(issues.filter((i) => i.level === 'warning'));
	const byField = $derived(new Map(issues.filter((i) => i.field && i.level === 'error').map((i) => [i.field, i.message])));
	const problem = (field: string) => (attempted ? (byField.get(field) ?? '') : '');

	const difficultyItems = DIFFICULTIES.map((d) => ({ value: d, label: d[0].toUpperCase() + d.slice(1) }));
	const categoryListId = `spec-categories-${Math.random().toString(36).slice(2, 8)}`;

	function setView(next: 'form' | 'json') {
		if (next === view) return;
		if (next === 'json') {
			jsonText = draftToJson(draft);
			jsonError = '';
			view = 'json';
			return;
		}
		if (applyJson()) view = 'form';
	}

	/** Parse the JSON view into the draft; false (and a message) when it can't. */
	function applyJson(): boolean {
		try {
			const next = jsonToDraft(jsonText);
			// The slug of an existing spec never changes.
			if (!isNew) next.slug = draft.slug;
			draft = next;
			slugTouched = true;
			jsonError = '';
			return true;
		} catch (e) {
			jsonError = (e as Error).message;
			return false;
		}
	}

	function submit() {
		if (view === 'json' && !applyJson()) return;
		attempted = true;
		if (errors.length) return;
		onSave(draftToPayload(draft), isNew);
	}

	function patchField(key: number, change: { name?: string; type?: string }) {
		draft.fields = draft.fields.map((f) => (f.key === key ? { ...f, ...change } : f));
	}

	const title = $derived(readonly ? draft.name || 'App spec' : isNew ? (copy ? 'Duplicate app spec' : 'New app spec') : `Edit ${initial?.name ?? 'app spec'}`);
</script>

<Card.Root>
	<Card.Header class="border-b">
		<div class="flex items-start justify-between gap-2">
			<div class="min-w-0 space-y-1">
				<Card.Title class="flex flex-wrap items-center gap-2">
					{title}
					{#if readonly}<Badge variant="secondary" class="h-5 px-1.5 text-[11px]">Catalog · read-only</Badge>{/if}
				</Card.Title>
				<Card.Description>
					{#if readonly}
						Catalog specs are shared by everyone; duplicate one to change it.
					{:else}
						The brief quotes this verbatim. Agent runs are graded on the endpoints and data model.
					{/if}
				</Card.Description>
			</div>
			<Button variant="ghost" size="icon" class="size-8 shrink-0" aria-label="Close editor" onclick={onCancel}>
				<XIcon />
			</Button>
		</div>
		<Segmented
			size="sm"
			aria-label="Editor view"
			value={view}
			items={[
				{ value: 'form', label: 'Form' },
				{ value: 'json', label: 'JSON' },
			]}
			onValueChange={(v) => setView(v as 'form' | 'json')}
		/>
	</Card.Header>

	<Card.Content class="space-y-6">
		{#if view === 'json'}
			<div class="space-y-1.5">
				<Label for="spec-json" class="text-xs">Spec JSON</Label>
				<Textarea
					id="spec-json"
					bind:value={jsonText}
					readonly={readonly}
					rows={24}
					aria-invalid={!!jsonError}
					class="bg-muted/20 font-mono text-[11px] leading-normal"
				/>
				<p class="text-[11px] text-muted-foreground">
					Same shape as the files in <span class="font-mono">backend/generation/data/requirements/</span>; paste one to start from it.
				</p>
				{#if jsonError}<p class="text-xs text-danger-fg">{jsonError}</p>{/if}
			</div>
		{:else}
			<!-- Basics -->
			<section class="space-y-3" aria-labelledby="spec-basics">
				<SectionHeading id="spec-basics" size="sm" level={3} title="Basics" />
				<div class="grid gap-3 sm:grid-cols-2">
					<div class="space-y-1.5">
						<Label for="spec-name" class="text-xs">Name</Label>
						<Input
							id="spec-name"
							bind:value={draft.name}
							readonly={readonly}
							aria-invalid={!!problem('spec-name')}
							placeholder="e.g. Book Library"
							class="h-9 text-xs"
							oninput={() => {
								if (isNew && !slugTouched) draft.slug = specSlugFromName(draft.name);
							}}
						/>
						{#if problem('spec-name')}<p class="text-[11px] text-danger-fg">{problem('spec-name')}</p>{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="spec-slug" class="text-xs">Slug</Label>
						<Input
							id="spec-slug"
							bind:value={draft.slug}
							disabled={!isNew}
							readonly={readonly}
							aria-invalid={!!problem('spec-slug')}
							oninput={() => (slugTouched = true)}
							placeholder="e.g. crud_book_library"
							class="h-9 font-mono text-xs"
						/>
						{#if problem('spec-slug')}<p class="text-[11px] text-danger-fg">{problem('spec-slug')}</p>{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="spec-category" class="text-xs">Category</Label>
						<Input id="spec-category" bind:value={draft.category} readonly={readonly} list={categoryListId} placeholder="e.g. CRUD" class="h-9 text-xs" />
						<datalist id={categoryListId}>
							{#each categories as c (c)}<option value={c}></option>{/each}
						</datalist>
					</div>
					<div class="space-y-1.5">
						<Label for="spec-difficulty" class="text-xs">Difficulty</Label>
						<Select.Simple
							id="spec-difficulty"
							value={draft.difficulty}
							items={difficultyItems}
							placeholder="Unrated"
							clearLabel="Unrated"
							disabled={readonly}
							onValueChange={(v) => (draft.difficulty = v as AppSpecDifficulty)}
						/>
					</div>
					<div class="space-y-1.5 sm:col-span-2">
						<Label for="spec-desc" class="text-xs">Description</Label>
						<Textarea id="spec-desc" bind:value={draft.description} readonly={readonly} rows={2} placeholder="What the app is for, in a sentence or two" class="text-xs leading-relaxed" />
					</div>
				</div>
			</section>

			<!-- Requirements -->
			<section class="space-y-3" aria-labelledby="spec-reqs">
				<SectionHeading id="spec-reqs" size="sm" level={3} title="Requirements" lead="One per line. Stated to the agent; not checked directly." />
				<div class="grid gap-3 sm:grid-cols-2">
					<div class="space-y-1.5">
						<Label for="spec-backend" class="text-xs">Backend</Label>
						<Textarea id="spec-backend" bind:value={draft.backend} readonly={readonly} rows={6} placeholder={'Book model: id, title, author\nSearch with ?query='} class="bg-muted/20 font-mono text-[11px] leading-normal" />
					</div>
					<div class="space-y-1.5">
						<Label for="spec-frontend" class="text-xs">Frontend</Label>
						<Textarea id="spec-frontend" bind:value={draft.frontend} readonly={readonly} rows={6} placeholder={'Book list table\nAdd book form (signed-in only)'} class="bg-muted/20 font-mono text-[11px] leading-normal" />
					</div>
					<div class="space-y-1.5 sm:col-span-2">
						<Label for="spec-admin" class="text-xs">Admin <span class="font-normal text-muted-foreground">(optional)</span></Label>
						<Textarea id="spec-admin" bind:value={draft.admin} readonly={readonly} rows={3} placeholder="Admin dashboard with totals" class="bg-muted/20 font-mono text-[11px] leading-normal" />
					</div>
				</div>
			</section>

			<!-- Data model -->
			<section class="space-y-3" aria-labelledby="spec-model">
				<SectionHeading id="spec-model" size="sm" level={3} title="Data model" />
				<div class="space-y-1.5 sm:w-1/2">
					<Label for="spec-model-name" class="text-xs">Model name</Label>
					<Input id="spec-model-name" bind:value={draft.modelName} readonly={readonly} aria-invalid={!!problem('spec-model-name')} placeholder="e.g. Book" class="h-9 text-xs" />
					{#if problem('spec-model-name')}<p class="text-[11px] text-danger-fg">{problem('spec-model-name')}</p>{/if}
				</div>
				<div class="space-y-2">
					{#each draft.fields as f, i (f.key)}
						{@const nameProblem = problem(`field-${f.key}-name`)}
						{@const typeProblem = problem(`field-${f.key}-type`)}
						<div class="flex items-start gap-2">
							<div class="flex-1 space-y-1">
								<Input
									value={f.name}
									readonly={readonly}
									aria-label="Field {i + 1} name"
									aria-invalid={!!nameProblem}
									placeholder="title"
									class="h-8 font-mono text-xs"
									oninput={(ev) => patchField(f.key, { name: ev.currentTarget.value })}
								/>
								{#if nameProblem}<p class="text-[11px] text-danger-fg">{nameProblem}</p>{/if}
							</div>
							<div class="flex-[2] space-y-1">
								<Input
									value={f.type}
									readonly={readonly}
									aria-label="Field {i + 1} type"
									aria-invalid={!!typeProblem}
									placeholder="string (required, max 200)"
									class="h-8 text-xs"
									oninput={(ev) => patchField(f.key, { type: ev.currentTarget.value })}
								/>
								{#if typeProblem}<p class="text-[11px] text-danger-fg">{typeProblem}</p>{/if}
							</div>
							{#if !readonly}
								<Button
									variant="ghost"
									size="sm"
									class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
									aria-label="Remove field {i + 1}"
									title="Remove"
									onclick={() => (draft.fields = draft.fields.filter((x) => x.key !== f.key))}
								>
									<Trash2 />
								</Button>
							{/if}
						</div>
					{/each}
					{#if !readonly}
						<Button variant="outline" size="sm" onclick={() => (draft.fields = [...draft.fields, emptyField()])}><Plus /> Add field</Button>
					{:else if draft.fields.length === 0}
						<p class="text-xs text-muted-foreground">No fields.</p>
					{/if}
				</div>
			</section>

			<!-- Endpoints -->
			<section class="space-y-3" aria-labelledby="spec-endpoints">
				<SectionHeading
					id="spec-endpoints"
					size="sm"
					level={3}
					title="API endpoints"
					count={draft.endpoints.length}
					lead="Each one is called by the hidden suite and must answer 2xx with the documented response fields."
				/>
				<EndpointListEditor bind:endpoints={draft.endpoints} prefix="ep" label="Endpoint" {readonly} {problem} />
			</section>

			<section class="space-y-3" aria-labelledby="spec-admin-endpoints">
				<SectionHeading id="spec-admin-endpoints" size="sm" level={3} title="Admin endpoints" count={draft.adminEndpoints.length} />
				<EndpointListEditor bind:endpoints={draft.adminEndpoints} prefix="aep" label="Admin endpoint" {readonly} {problem} />
			</section>
		{/if}

		{#if !readonly && (warnings.length || (attempted && errors.length))}
			<div class="space-y-1 rounded-md border border-border bg-muted/20 p-3 text-xs" role="status">
				{#if attempted && errors.length}
					<p class="font-medium text-danger-fg">Fix {errors.length} {errors.length === 1 ? 'problem' : 'problems'} to save:</p>
					<ul class="list-disc space-y-0.5 pl-5 text-danger-fg">
						{#each errors as e (e.message)}<li>{e.message}</li>{/each}
					</ul>
				{/if}
				{#if warnings.length}
					<p class="flex items-center gap-1.5 font-medium text-warning-fg"><TriangleAlert class="size-3.5" /> Can be saved, but:</p>
					<ul class="list-disc space-y-0.5 pl-5 text-muted-foreground">
						{#each warnings as w (w.message)}<li>{w.message}</li>{/each}
					</ul>
				{/if}
			</div>
		{/if}

		{#if error}
			<ErrorAlert message={error} />
		{/if}

		<div class="flex justify-end gap-2 border-t pt-4">
			{#if readonly}
				<Button variant="outline" size="sm" onclick={onCancel}>Close</Button>
				{#if onDuplicate}<Button size="sm" onclick={onDuplicate}><Copy /> Duplicate to edit</Button>{/if}
			{:else}
				<Button variant="outline" size="sm" onclick={onCancel}>Cancel</Button>
				<Button size="sm" onclick={submit} disabled={saving}>
					{#if saving}
						<LoaderCircle class="animate-spin" /> Saving…
					{:else}
						<Save /> {isNew ? 'Create app spec' : 'Save app spec'}
					{/if}
				</Button>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
