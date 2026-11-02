<script lang="ts">
	/**
	 * ObservationEditor — dialog to report (or edit) a bug / vulnerability for one
	 * app: title, category, the tester's own severity ("Your severity"),
	 * location (a path such as /admin/users or an http(s) URL), description,
	 * steps, and screenshots. Screenshots can be pasted from the clipboard,
	 * dropped, or picked (PNG / JPEG / GIF / WebP, ≤ 5 MiB, ≤ 5 per report);
	 * they upload after the report is saved and show as thumbnails.
	 *
	 * Props:
	 * - `open` (bindable), `assignmentId`, `kind`, `slot`, `blockId`, `appLabel`.
	 * - `observation`: the report being edited, or null for a new one.
	 * - `checklist`: security checklist items a vulnerability can be linked to.
	 * - `onsaved(observation)`: after every persisted change (save, upload, removed screenshot).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ImagePlus from '@lucide/svelte/icons/image-plus';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import X from '@lucide/svelte/icons/x';
	import {
		createObservation,
		deleteAttachment,
		portalAttachmentUrl,
		updateObservation,
		uploadAttachment,
	} from '$lib/api/human-eval-portal';
	import type {
		Observation,
		ObservationAttachment,
		ObservationKind,
		SecurityChecklistItem,
		TesterSeverity,
	} from '$lib/api/types/human-eval';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { cn } from '$lib/utils';
	import {
		IMAGE_ACCEPT,
		KIND_LABELS,
		LIMITS,
		LOCATION_PLACEHOLDER,
		MAX_ATTACHMENTS,
		TESTER_SEVERITIES,
		categoriesFor,
		draftErrors,
		draftFrom,
		draftToInput,
		draftToPatch,
		emptyDraft,
		formatBytes,
		imageFileIssue,
		pastedImages,
		uploadName,
		type ObservationDraft,
		type Slot,
	} from './observations';
	import { workbenchError } from './workbench';

	interface Props {
		open: boolean;
		assignmentId: string;
		kind: ObservationKind;
		slot: Slot;
		blockId: string;
		appLabel: string;
		observation?: Observation | null;
		checklist?: SecurityChecklistItem[];
		onsaved: (observation: Observation) => void;
	}

	let {
		open = $bindable(false),
		assignmentId,
		kind,
		slot,
		blockId,
		appLabel,
		observation = null,
		checklist = [],
		onsaved,
	}: Props = $props();

	const uid = $props.id();

	interface PendingFile {
		id: number;
		file: File;
		url: string;
	}

	let draft = $state<ObservationDraft>(emptyDraft('bug', 'single', ''));
	let current = $state.raw<Observation | null>(null);
	let attachments = $state.raw<ObservationAttachment[]>([]);
	let pending = $state.raw<PendingFile[]>([]);
	/** Field errors show (and update live) once the participant tried to save. */
	let tried = $state(false);
	const fieldErrors = $derived<Record<string, string>>(tried ? draftErrors(draft) : {});
	let formError = $state('');
	let fileErrors = $state<string[]>([]);
	let saving = $state(false);
	let dragging = $state(false);
	let removing = $state<string | null>(null);
	let fileInput = $state<HTMLInputElement | null>(null);
	let fileSeq = 0;

	const categories = $derived(categoriesFor(draft.kind));
	const totalShots = $derived(attachments.length + pending.length);
	const heading = $derived(
		current ? `Edit ${KIND_LABELS[draft.kind].toLowerCase()} report` : `Report a ${KIND_LABELS[draft.kind].toLowerCase()}`
	);

	// (Re)initialise whenever the dialog opens.
	$effect(() => {
		if (!open) return;
		untrack(() => {
			current = observation;
			draft = observation ? draftFrom(observation) : emptyDraft(kind, slot, blockId);
			attachments = observation?.attachments ?? [];
			clearPending();
			tried = false;
			formError = '';
			fileErrors = [];
		});
	});

	// Free object URLs when the dialog closes or the component goes away.
	$effect(() => {
		if (open) return;
		untrack(clearPending);
	});
	$effect(() => () => clearPending());

	function clearPending() {
		for (const p of pending) URL.revokeObjectURL(p.url);
		pending = [];
	}

	function addFiles(files: Iterable<File>) {
		const problems: string[] = [];
		const next = [...pending];
		for (const file of files) {
			const issue = imageFileIssue(file);
			if (issue) {
				problems.push(issue);
				continue;
			}
			if (attachments.length + next.length >= MAX_ATTACHMENTS) {
				problems.push(`At most ${MAX_ATTACHMENTS} screenshots per report.`);
				break;
			}
			next.push({ id: ++fileSeq, file, url: URL.createObjectURL(file) });
		}
		pending = next;
		fileErrors = problems;
	}

	function removePending(id: number) {
		const item = pending.find((p) => p.id === id);
		if (item) URL.revokeObjectURL(item.url);
		pending = pending.filter((p) => p.id !== id);
	}

	async function removeExisting(att: ObservationAttachment) {
		if (!current) return;
		removing = att.id;
		try {
			await deleteAttachment(att.id);
			attachments = attachments.filter((a) => a.id !== att.id);
			current = { ...current, attachments };
			onsaved(current);
		} catch (e) {
			toast.error(workbenchError(e, 'Could not remove the screenshot.'));
		} finally {
			removing = null;
		}
	}

	function onPaste(event: ClipboardEvent) {
		const files = pastedImages(event.clipboardData);
		if (files.length === 0) return;
		// Pasting into a text field with text on the clipboard keeps the text too.
		const target = event.target as HTMLElement | null;
		const intoText = !!target?.closest('input, textarea') && !!event.clipboardData?.getData('text/plain');
		if (!intoText) event.preventDefault();
		addFiles(files);
	}

	function onDrop(event: DragEvent) {
		event.preventDefault();
		dragging = false;
		addFiles(Array.from(event.dataTransfer?.files ?? []));
	}

	function onPick(event: Event) {
		const input = event.currentTarget as HTMLInputElement;
		addFiles(Array.from(input.files ?? []));
		input.value = '';
	}

	async function save(event?: SubmitEvent) {
		event?.preventDefault();
		if (saving) return;
		tried = true;
		if (Object.keys(draftErrors(draft)).length > 0) return;
		saving = true;
		formError = '';
		try {
			let saved: Observation;
			try {
				saved = current
					? await updateObservation(current.id, draftToPatch(draft))
					: await createObservation(assignmentId, draftToInput(draft));
			} catch (e) {
				formError = workbenchError(e, 'Could not save the report.');
				return;
			}
			current = saved;
			attachments = saved.attachments ?? attachments;
			// Upload screenshots one by one; keep the ones that failed for another try.
			const failed: string[] = [];
			const stillPending: PendingFile[] = [];
			for (const [i, p] of pending.entries()) {
				try {
					const att = await uploadAttachment(saved.id, p.file, uploadName(p.file, attachments.length + i));
					attachments = [...attachments, att];
					URL.revokeObjectURL(p.url);
				} catch (e) {
					failed.push(workbenchError(e, `Could not upload ${p.file.name || 'a screenshot'}.`));
					stillPending.push(p);
				}
			}
			pending = stillPending;
			current = { ...saved, attachments };
			onsaved(current);
			if (failed.length > 0) {
				fileErrors = failed;
				formError = 'The report is saved, but some screenshots did not upload. Try again or remove them.';
				return;
			}
			// No success toast: the list shows the report, and a toast would cover the footer actions.
			open = false;
		} finally {
			saving = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-h-[92dvh] max-w-2xl overflow-y-auto p-0">
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<form class="space-y-4 p-5 sm:p-6" onsubmit={save} onpaste={onPaste}>
			<Dialog.Header>
				<Dialog.Title>{heading}</Dialog.Title>
				<Dialog.Description>
					For <b class="font-medium text-foreground">{appLabel}</b>. Describe what you saw so someone else can
					reproduce it.
				</Dialog.Description>
			</Dialog.Header>

			<div class="space-y-1.5">
				<label for="{uid}-title" class="text-sm font-medium">Title</label>
				<Input
					id="{uid}-title"
					bind:value={draft.title}
					maxlength={LIMITS.title}
					placeholder={draft.kind === 'vulnerability' ? 'e.g. Any user can delete other users’ posts' : 'e.g. Saving the form shows a 500 error'}
					aria-invalid={!!fieldErrors.title}
					aria-describedby={fieldErrors.title ? `${uid}-title-err` : undefined}
				/>
				{#if fieldErrors.title}<p id="{uid}-title-err" class="text-[11px] font-medium text-danger-fg">{fieldErrors.title}</p>{/if}
			</div>

			<div class="grid gap-4 sm:grid-cols-2">
				{#if draft.kind === 'vulnerability' && checklist.length > 0}
					<div class="space-y-1.5">
						<label for="{uid}-item" class="text-sm font-medium">Checklist item</label>
						<Select.Simple
							id="{uid}-item"
							bind:value={draft.checklist_item_id}
							clearLabel="None"
							placeholder="None"
							items={checklist.map((c) => ({ value: c.id, label: c.label, description: c.category }))}
						/>
					</div>
				{/if}
				{#if categories.length > 0}
					<div class="space-y-1.5">
						<label for="{uid}-cat" class="text-sm font-medium">Category</label>
						<Select.Simple
							id="{uid}-cat"
							bind:value={draft.category}
							clearLabel="None"
							placeholder="Choose a category"
							items={categories}
						/>
					</div>
				{/if}
				<div class="space-y-1.5">
					<label for="{uid}-sev" class="text-sm font-medium">Your severity</label>
					<Select.Simple
						id="{uid}-sev"
						value={draft.severity}
						onValueChange={(v) => (draft.severity = v as TesterSeverity | '')}
						clearLabel="Not rated"
						placeholder="Not rated"
						aria-describedby="{uid}-sev-help"
						items={TESTER_SEVERITIES}
					/>
					<p id="{uid}-sev-help" class="text-[11px] text-muted-foreground">Your own judgement of how serious it is.</p>
				</div>
				{#if draft.kind === 'vulnerability'}
					<div class="space-y-1.5">
						<label for="{uid}-cwe" class="text-sm font-medium">CWE <span class="font-normal text-muted-foreground">(optional)</span></label>
						<Input
							id="{uid}-cwe"
							bind:value={draft.cwe}
							maxlength={LIMITS.cwe}
							placeholder="e.g. CWE-639"
							aria-invalid={!!fieldErrors.cwe}
						/>
						{#if fieldErrors.cwe}<p class="text-[11px] font-medium text-danger-fg">{fieldErrors.cwe}</p>{/if}
					</div>
				{/if}
			</div>

			<div class="space-y-1.5">
				<label for="{uid}-loc" class="text-sm font-medium">Where <span class="font-normal text-muted-foreground">(optional)</span></label>
				<Input
					id="{uid}-loc"
					bind:value={draft.location}
					maxlength={LIMITS.location}
					placeholder={LOCATION_PLACEHOLDER}
					aria-invalid={!!fieldErrors.location}
					aria-describedby="{uid}-loc-help"
				/>
				<p id="{uid}-loc-help" class={cn('text-[11px]', fieldErrors.location ? 'font-medium text-danger-fg' : 'text-muted-foreground')}>
					{fieldErrors.location || 'A page path starting with “/”, or a full http(s) address. Put the HTTP method (GET, POST…) in the description.'}
				</p>
			</div>

			<div class="space-y-1.5">
				<label for="{uid}-desc" class="text-sm font-medium">What happened</label>
				<Textarea
					id="{uid}-desc"
					rows={3}
					bind:value={draft.description}
					maxlength={LIMITS.description}
					placeholder={draft.kind === 'vulnerability' ? 'What can an attacker do, and why is it a problem?' : 'What did you expect, and what happened instead?'}
				/>
			</div>

			<div class="space-y-1.5">
				<label for="{uid}-steps" class="text-sm font-medium">Steps to reproduce <span class="font-normal text-muted-foreground">(optional)</span></label>
				<Textarea
					id="{uid}-steps"
					rows={3}
					bind:value={draft.steps}
					maxlength={LIMITS.steps}
					placeholder={'1. Log in as a normal user\n2. Open …\n3. …'}
				/>
			</div>

			<fieldset class="space-y-2">
				<legend class="text-sm font-medium">
					Screenshots <span class="font-normal text-muted-foreground">({totalShots}/{MAX_ATTACHMENTS})</span>
				</legend>
				<div
					role="group"
					aria-label="Screenshot drop zone"
					class={cn(
						'flex flex-wrap items-center gap-3 rounded-md border border-dashed border-border px-3 py-3 text-xs text-muted-foreground transition-colors',
						dragging && 'border-primary bg-primary/5'
					)}
					ondragover={(e) => {
						e.preventDefault();
						dragging = true;
					}}
					ondragleave={() => (dragging = false)}
					ondrop={onDrop}
				>
					<Button
						type="button"
						variant="outline"
						size="sm"
						disabled={totalShots >= MAX_ATTACHMENTS}
						onclick={() => fileInput?.click()}
					>
						<ImagePlus />
						Add images
					</Button>
					<span>Paste a screenshot with <kbd class="rounded border border-border px-1 font-mono">Ctrl</kbd>+<kbd class="rounded border border-border px-1 font-mono">V</kbd> or drop images here. PNG, JPEG, GIF or WebP up to 5 MB.</span>
					<input
						bind:this={fileInput}
						type="file"
						accept={IMAGE_ACCEPT}
						multiple
						class="sr-only"
						tabindex="-1"
						aria-hidden="true"
						onchange={onPick}
					/>
				</div>
				{#if attachments.length + pending.length > 0}
					<ul class="flex flex-wrap gap-2">
						{#each attachments as att, i (att.id)}
							<li class="group relative">
								<a
									href={portalAttachmentUrl(att.id)}
									target="_blank"
									rel="noopener noreferrer"
									class="block overflow-hidden rounded-md border border-border"
									title="Open full size"
								>
									<img src={portalAttachmentUrl(att.id)} alt="Screenshot {i + 1}" class="h-20 w-28 object-cover" loading="lazy" />
								</a>
								<button
									type="button"
									class="absolute right-1 top-1 flex size-6 cursor-pointer items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-sm hover:text-foreground"
									aria-label="Remove screenshot {i + 1}"
									disabled={removing === att.id}
									onclick={() => removeExisting(att)}
								>
									{#if removing === att.id}<LoaderCircle class="size-3.5 animate-spin" />{:else}<X class="size-3.5" />{/if}
								</button>
							</li>
						{/each}
						{#each pending as p, i (p.id)}
							<li class="relative">
								<img
									src={p.url}
									alt="New screenshot {i + 1}"
									class="h-20 w-28 rounded-md border border-dashed border-primary/50 object-cover"
								/>
								<span class="absolute bottom-1 left-1 rounded bg-card/90 px-1 font-mono text-[11px] text-muted-foreground">
									{formatBytes(p.file.size)}
								</span>
								<button
									type="button"
									class="absolute right-1 top-1 flex size-6 cursor-pointer items-center justify-center rounded-full border border-border bg-card text-muted-foreground shadow-sm hover:text-foreground"
									aria-label="Remove new screenshot {i + 1}"
									onclick={() => removePending(p.id)}
								>
									<X class="size-3.5" />
								</button>
							</li>
						{/each}
					</ul>
				{/if}
				{#if fileErrors.length > 0}
					<ul class="space-y-0.5 text-[11px] font-medium text-danger-fg" role="alert">
						{#each fileErrors as message, i (i)}<li>{message}</li>{/each}
					</ul>
				{/if}
			</fieldset>

			{#if formError}
				<p role="alert" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-danger-fg">{formError}</p>
			{/if}

			<Dialog.Footer>
				<Button type="button" variant="outline" size="sm" disabled={saving} onclick={() => (open = false)}>
					{current && pending.length === 0 && formError ? 'Close' : 'Cancel'}
				</Button>
				<Button type="submit" size="sm" disabled={saving} aria-busy={saving}>
					{#if saving}<LoaderCircle class="animate-spin" />{/if}
					{current ? 'Save changes' : 'Save report'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
