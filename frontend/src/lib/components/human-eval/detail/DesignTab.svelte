<script lang="ts">
	/**
	 * DesignTab — edit the task: basics (title, description, instructions,
	 * schedule), mode, design (DesignEditor) and settings (SettingsEditor).
	 *
	 * Draft: everything is editable; the design is validated live
	 * (`validate-design/`, debounced) and errors land on their fields.
	 * After the first open the design is frozen: DesignEditor is read-only and
	 * SettingsEditor runs `frozen` (only access, hosting on-demand / idle stop /
	 * max running, allocation limits and — unless the plan excludes speeders —
	 * quality stay editable). Title, description and instructions stay editable;
	 * changes after opening are recorded in the protocol log. Archived tasks are
	 * read-only. If the task opens while the form holds unsaved design, mode or
	 * frozen-setting edits, those are replaced by what was frozen (with a toast).
	 *
	 * Props: `task`, `environment`, `onchange(task)` (the saved TaskDetail),
	 * `onunsaved(text)` — unsaved edits an open would discard, as
	 * describeFreezeLosses text ('' when none; also '' when the tab unmounts).
	 */
	import { onDestroy, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import DesignEditor from '$lib/components/human-eval/design/DesignEditor.svelte';
	import MarkdownField from '$lib/components/human-eval/design/MarkdownField.svelte';
	import { classifyPath } from '$lib/components/human-eval/design/errors';
	import SettingsEditor from '$lib/components/human-eval/SettingsEditor.svelte';
	import * as Card from '$lib/components/ui/card';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Textarea } from '$lib/components/ui/textarea';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getStudyResults } from '$lib/api/v3/studies';
	import {
		humanEvalErrorBody,
		updateTask,
		validateDesign,
		type HumanEvalEnvironment,
		type TaskDesign,
		type TaskDetail,
		type TaskMode,
		type TaskSettings,
		type TaskUpdateInput,
	} from '$lib/api/human-eval';
	import { formatDateTime } from '$lib/utils/formatters';
	import { MODE_LABELS, shortHash } from '../format';
	import {
		describeFreezeLosses,
		editableAfterFreeze,
		editableAfterFreezeText,
		editsLostOnFreeze,
		qualityLockText,
		rebaseOnFreeze,
		speedingExcluded,
	} from '../freeze';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Lock from '@lucide/svelte/icons/lock';

	interface Props {
		task: TaskDetail;
		environment: HumanEvalEnvironment | null;
		onchange?: (task: TaskDetail) => void;
		onunsaved?: (freezeLosses: string) => void;
	}

	let { task, environment, onchange, onunsaved }: Props = $props();

	type Draft = {
		title: string;
		description: string;
		instructions: string;
		opens_at: string;
		closes_at: string;
		mode: TaskMode;
		design: TaskDesign;
		settings: TaskSettings;
	};

	function toLocalInput(iso: string | null): string {
		if (!iso) return '';
		const d = new Date(iso);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
	}
	function fromLocalInput(value: string): string | null {
		return value ? new Date(value).toISOString() : null;
	}

	function draftOf(t: TaskDetail): Draft {
		return {
			title: t.title,
			description: t.description,
			instructions: t.instructions,
			opens_at: toLocalInput(t.opens_at),
			closes_at: toLocalInput(t.closes_at),
			mode: t.mode,
			design: structuredClone($state.snapshot(t.design) as TaskDesign),
			settings: structuredClone($state.snapshot(t.settings) as TaskSettings),
		};
	}

	let draft = $state<Draft>(untrack(() => draftOf(task)));
	let baseline = $state(untrack(() => JSON.stringify(draftOf(task))));
	let baseVersion = untrack(() => `${task.id}|${task.updated_at}|${task.frozen_at}`);

	const dirty = $derived(JSON.stringify(draft) !== baseline);
	const frozen = $derived(!!task.frozen_at);
	const archived = $derived(task.status === 'archived');
	/** Quality is frozen too: the stored (frozen, once opened) plan excludes speeders. */
	const qualityLocked = $derived(speedingExcluded(task.design));

	// Unsaved edits an open would not freeze (the page warns before opening a draft).
	// Opening freezes the saved design, so its plan decides which settings stay editable.
	const freezeLosses = $derived(
		frozen || !dirty ? [] : editsLostOnFreeze(draft, JSON.parse(baseline) as Draft, editableAfterFreeze(task.design))
	);
	const freezeLossText = $derived(describeFreezeLosses(freezeLosses));
	$effect(() => {
		const text = freezeLossText;
		untrack(() => onunsaved?.(text));
	});
	onDestroy(() => onunsaved?.(''));

	// A newer task (saved here, a status change, a quiet reload) replaces the
	// form unless it holds unsaved edits.
	let staleNotice = $state(false);
	let baseFrozen = untrack(() => !!task.frozen_at);
	$effect(() => {
		const version = `${task.id}|${task.updated_at}|${task.frozen_at}`;
		untrack(() => {
			if (version === baseVersion) return;
			baseVersion = version;
			const justFrozen = !baseFrozen && !!task.frozen_at;
			baseFrozen = !!task.frozen_at;
			if (dirty && justFrozen) keepOnlyPostFreezeEdits();
			if (dirty) {
				staleNotice = true;
				return;
			}
			reset();
		});
	});

	/**
	 * The task opened (froze) under unsaved edits: what was frozen is the saved design, so
	 * show that and drop the design / mode / frozen-setting edits (saving them would be
	 * skipped or refused). Edits an opened task accepts stay in the form.
	 */
	function keepOnlyPostFreezeEdits() {
		const rebased = rebaseOnFreeze(
			$state.snapshot(draft) as Draft,
			JSON.parse(baseline) as Draft,
			draftOf(task),
			editableAfterFreeze(task.design)
		);
		if (rebased.dropped.length === 0) return;
		draft = rebased.draft;
		baseline = JSON.stringify(rebased.base);
		serverErrors = [];
		const lost = describeFreezeLosses(rebased.dropped);
		// Not from inside the effect flush (sonner's Toaster trips over a toast added there).
		setTimeout(() => toast.warning(`The task opened with its saved design: your unsaved changes to ${lost} were not applied.`), 0);
	}

	function reset() {
		draft = draftOf(task);
		baseline = JSON.stringify(draft);
		serverErrors = [];
		staleNotice = false;
	}

	// ── live validation (draft only) ────────────────────────────────
	let validationErrors = $state.raw<{ path: string; message: string }[]>([]);
	let validationOk = $state<boolean | null>(null);
	let validating = $state(false);
	let validateCtl: AbortController | null = null;
	let validateTimer: ReturnType<typeof setTimeout> | undefined;

	const validationKey = $derived(frozen ? '' : JSON.stringify([draft.mode, draft.design, draft.settings]));
	$effect(() => {
		const key = validationKey;
		if (!key) return;
		untrack(() => {
			clearTimeout(validateTimer);
			validateTimer = setTimeout(() => void runValidation(), 600);
		});
	});

	async function runValidation() {
		validateCtl?.abort();
		const mine = new AbortController();
		validateCtl = mine;
		validating = true;
		try {
			const res = await validateDesign(
				{ mode: draft.mode, design: $state.snapshot(draft.design) as TaskDesign, settings: $state.snapshot(draft.settings) as TaskSettings },
				{ signal: mine.signal }
			);
			validationErrors = res.errors;
			validationOk = res.ok;
		} catch (e) {
			if (isAbortError(e) || mine.signal.aborted) return;
			// Validation is advisory; saving reports the authoritative errors.
			validationOk = null;
		} finally {
			if (validateCtl === mine) validating = false;
		}
	}

	onDestroy(() => {
		clearTimeout(validateTimer);
		validateCtl?.abort();
	});

	// ── baseline options (vs_baseline pairing) ──────────────────────
	// The linked study's StudyConditions (the results leaderboard lists every condition of the
	// active plan with its id and the pre-registered `is_baseline` flag). Without them the
	// pairing falls back to the study's baseline condition on the server.
	let baselineOptions = $state<{ id: number; label: string }[]>([]);
	$effect(() => {
		const studyId = task.study?.id;
		if (!studyId) {
			baselineOptions = [];
			return;
		}
		untrack(() => {
			getStudyResults(studyId)
				.then((res) => {
					if (task.study?.id !== studyId) return;
					baselineOptions = [...res.leaderboard]
						.sort((x, y) => x.index - y.index)
						.map((c) => ({
							id: c.condition_id,
							label: `${c.label || `Condition #${c.condition_id}`}${c.is_baseline ? ' (pre-registered baseline)' : ''}`,
						}));
				})
				.catch(() => (baselineOptions = []));
		});
	});

	// ── save ────────────────────────────────────────────────────────
	let saving = $state(false);
	let serverErrors = $state.raw<{ path: string; message: string }[]>([]);
	let saveError = $state('');

	const errors = $derived(serverErrors.length ? serverErrors : frozen ? [] : validationErrors);
	const designErrors = $derived(errors.filter((e) => classifyPath(e.path ?? '').area === 'design'));
	const otherErrors = $derived(errors.filter((e) => classifyPath(e.path ?? '').area !== 'design'));

	function changedPatch(): TaskUpdateInput {
		const base = JSON.parse(baseline) as Draft;
		const patch: TaskUpdateInput = {};
		if (draft.title !== base.title) patch.title = draft.title.trim();
		if (draft.description !== base.description) patch.description = draft.description;
		if (draft.instructions !== base.instructions) patch.instructions = draft.instructions;
		if (draft.opens_at !== base.opens_at) patch.opens_at = fromLocalInput(draft.opens_at);
		if (draft.closes_at !== base.closes_at) patch.closes_at = fromLocalInput(draft.closes_at);
		if (!frozen && draft.mode !== base.mode) patch.mode = draft.mode;
		if (!frozen && JSON.stringify(draft.design) !== JSON.stringify(base.design)) {
			patch.design = $state.snapshot(draft.design) as TaskDesign;
		}
		if (JSON.stringify(draft.settings) !== JSON.stringify(base.settings)) {
			patch.settings = $state.snapshot(draft.settings) as TaskSettings;
		}
		return patch;
	}

	async function save() {
		if (!draft.title.trim()) {
			saveError = 'The title is required.';
			return;
		}
		const patch = changedPatch();
		if (Object.keys(patch).length === 0) {
			reset();
			return;
		}
		saving = true;
		saveError = '';
		serverErrors = [];
		try {
			const next = await updateTask(task.id, patch);
			baseVersion = `${next.id}|${next.updated_at}|${next.frozen_at}`;
			baseFrozen = !!next.frozen_at;
			onchange?.(next);
			draft = draftOf(next);
			baseline = JSON.stringify(draft);
			staleNotice = false;
			toast.success(frozen && (patch.title || patch.description !== undefined || patch.instructions !== undefined) ? 'Saved — recorded in the protocol log' : 'Saved');
		} catch (e) {
			const body = humanEvalErrorBody(e);
			if (body) {
				serverErrors = body.errors;
				saveError =
					body.code === 'design_frozen'
						? `${body.detail || 'Some of these settings are frozen.'} Only ${editableAfterFreezeText(task.design)} can change after opening.`
						: body.detail || 'Could not save the task.';
			} else {
				saveError = formatApiError(e, 'Could not save the task.');
			}
		} finally {
			saving = false;
		}
	}

	const MODE_ITEMS = [
		{ value: 'single', label: MODE_LABELS.single, title: 'Each participant evaluates one app at a time' },
		{ value: 'pairwise', label: MODE_LABELS.pairwise, title: 'Each participant compares two apps side by side' },
	];
	let modeValue = $state(untrack(() => draft.mode as string));
	$effect(() => {
		modeValue = draft.mode;
	});
	function onModeChange(value: string) {
		if (value === 'single' || value === 'pairwise') draft.mode = value;
	}
</script>

<div class="space-y-6 pb-16">
	{#if archived}
		<Alert>
			<Lock />
			<AlertDescription>This task is archived: unarchive it to change anything.</AlertDescription>
		</Alert>
	{:else if frozen}
		<Alert>
			<Lock />
			<AlertDescription>
				Design frozen {formatDateTime(task.frozen_at)} (hash <span class="font-mono">{shortHash(task.design_hash, 12)}</span>). Blocks,
				consent, questionnaires, the analysis plan and every setting that changes what is measured are locked.
				{editableAfterFreezeText(task.design).replace(/^./, (c) => c.toUpperCase())} stay editable.{qualityLocked ? ` ${qualityLockText(task.design)}` : ''}
				Title, description and instruction edits are recorded in the protocol log.
			</AlertDescription>
		</Alert>
	{/if}

	{#if staleNotice}
		<Alert class="border-warning/40 bg-warning/5">
			<CircleAlert class="text-warning-fg" />
			<div class="flex flex-wrap items-center justify-between gap-2">
				<AlertDescription>
					The task changed since you started editing (status or another tab). Saving sends only the fields you changed{frozen
						? ' that an opened task still accepts; the design itself is frozen'
						: ''}.
				</AlertDescription>
				<Button variant="outline" size="sm" onclick={reset}>Discard my edits</Button>
			</div>
		</Alert>
	{/if}

	<section class="space-y-3">
		<SectionHeading title="Basics" number="1" lead="What participants read on the join page, the task page and in the workbench." />
		<Card.Root>
			<Card.Content class="space-y-4">
				<div class="space-y-1.5">
					<Label for="he-title">Title</Label>
					<Input id="he-title" bind:value={draft.title} maxlength={200} disabled={archived} />
				</div>
				<div class="space-y-1.5">
					<Label for="he-description">Description</Label>
					<Textarea id="he-description" rows={2} bind:value={draft.description} disabled={archived} placeholder="Short public summary on the join page" />
				</div>
				<MarkdownField id="he-instructions" label="Instructions" bind:value={draft.instructions} rows={8} readOnly={archived} hint="Markdown. Shown on the task page and in the workbench." />
				<div class="grid gap-4 sm:grid-cols-2">
					<div class="space-y-1.5">
						<Label for="he-opens">Opens at (optional)</Label>
						<Input id="he-opens" type="datetime-local" bind:value={draft.opens_at} disabled={archived} />
						<p class="text-xs text-muted-foreground">Before this time an open task accepts nobody.</p>
					</div>
					<div class="space-y-1.5">
						<Label for="he-closes">Closes at (optional)</Label>
						<Input id="he-closes" type="datetime-local" bind:value={draft.closes_at} disabled={archived} />
						<p class="text-xs text-muted-foreground">After this time no new work starts; close the task to finish it.</p>
					</div>
				</div>
				<div class="space-y-1.5">
					<span class="text-sm font-medium" id="he-mode-label">Mode</span>
					{#if frozen || archived}
						<p class="text-sm">{MODE_LABELS[task.mode]}</p>
					{:else}
						<div><Segmented aria-labelledby="he-mode-label" items={MODE_ITEMS} bind:value={modeValue} onValueChange={onModeChange} /></div>
						<p class="text-xs text-muted-foreground">Pairwise designs use pairwise and bug-log blocks; switching modes may make blocks invalid.</p>
					{/if}
				</div>
			</Card.Content>
		</Card.Root>
	</section>

	<section class="space-y-3">
		<SectionHeading title="Design" number="2" lead="Evaluation blocks, consent, questionnaires and the pre-registered analysis plan.">
			{#snippet aside()}
				{#if !frozen && !archived}
					{#if validating}
						<span class="inline-flex items-center gap-1 text-xs text-muted-foreground"><LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />checking</span>
					{:else if validationOk === true && serverErrors.length === 0}
						<span class="inline-flex items-center gap-1 text-xs text-success-fg"><CircleCheck class="size-3.5" />valid</span>
					{:else if errors.length > 0}
						<span class="inline-flex items-center gap-1 text-xs text-danger-fg"><CircleAlert class="size-3.5" />{errors.length} to fix</span>
					{/if}
				{/if}
			{/snippet}
		</SectionHeading>
		<DesignEditor mode={draft.mode} bind:design={draft.design} {environment} readOnly={frozen || archived} errors={designErrors} />
	</section>

	<section class="space-y-3">
		<SectionHeading
			title="Settings"
			number="3"
			lead={frozen ? 'Only settings that don’t change what is measured stay editable after opening.' : 'Blinding, allocation, pairing, access, hosting and data quality.'}
		/>
		{#if otherErrors.length > 0}
			<Alert variant="destructive">
				<CircleAlert />
				<ul class="space-y-0.5 text-sm">
					{#each otherErrors as err (err.path + err.message)}
						<li><span class="font-mono text-xs">{err.path}</span>{err.path ? ': ' : ''}{err.message}</li>
					{/each}
				</ul>
			</Alert>
		{/if}
		<SettingsEditor mode={draft.mode} bind:settings={draft.settings} {frozen} lockQuality={frozen && qualityLocked} readOnly={archived} {baselineOptions} />
	</section>
</div>

{#if !archived && (dirty || saveError)}
	<div class="sticky bottom-3 z-20 mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-card px-4 py-2.5 shadow-[var(--shadow-pop)]">
		<p class="min-w-0 text-sm" role="status">
			{#if saveError}<span class="text-danger-fg">{saveError}</span>{:else}Unsaved changes{/if}
		</p>
		<div class="flex gap-2">
			<Button variant="outline" size="sm" disabled={saving || !dirty} onclick={reset}>Discard</Button>
			<Button size="sm" disabled={saving || !dirty} aria-busy={saving} onclick={save}>
				{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Save changes
			</Button>
		</div>
	</div>
{/if}
