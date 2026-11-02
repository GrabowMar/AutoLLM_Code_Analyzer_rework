<script lang="ts">
	/**
	 * ObservationList — the participant's own bug / vulnerability reports for one
	 * app, with "Report …" to add one, edit / delete per report and screenshot
	 * thumbnails. Everything the participant typed is rendered as text; a
	 * location becomes a link only when it is an absolute http(s) URL.
	 *
	 * Props:
	 * - `assignmentId`, `kind`, `slot`, `blockId`, `appLabel`, `readOnly`.
	 * - `observations`: the reports to show (already filtered to this kind/slot).
	 * - `checklist`: security checklist items (labels for linked vulnerabilities).
	 * - `onsaved(observation)` / `ondeleted(id)`: keep the workbench's list current.
	 * - `addLabel`: text of the add button.
	 */
	import { toast } from 'svelte-sonner';
	import Plus from '@lucide/svelte/icons/plus';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import { deleteObservation, portalAttachmentUrl } from '$lib/api/human-eval-portal';
	import type { Observation, ObservationKind, SecurityChecklistItem } from '$lib/api/types/human-eval';
	import { Button } from '$lib/components/ui/button';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import { severityColors } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import ObservationEditor from './ObservationEditor.svelte';
	import { KIND_LABELS, TESTER_SEVERITIES, categoryLabel, locationHref, type Slot } from './observations';
	import { workbenchError } from './workbench';

	interface Props {
		assignmentId: string;
		kind: ObservationKind;
		slot: Slot;
		blockId: string;
		appLabel: string;
		observations: Observation[];
		checklist?: SecurityChecklistItem[];
		readOnly?: boolean;
		addLabel?: string;
		onsaved: (observation: Observation) => void;
		ondeleted: (id: string) => void;
	}

	let {
		assignmentId,
		kind,
		slot,
		blockId,
		appLabel,
		observations,
		checklist = [],
		readOnly = false,
		addLabel,
		onsaved,
		ondeleted,
	}: Props = $props();

	let editorOpen = $state(false);
	let editing = $state.raw<Observation | null>(null);
	let confirmOpen = $state(false);
	let toDelete = $state.raw<Observation | null>(null);
	let deleting = $state(false);

	const severityLabel = (value: string) => TESTER_SEVERITIES.find((s) => s.value === value)?.label ?? value;
	const checklistLabel = (id: string) => checklist.find((c) => c.id === id)?.label ?? id;

	function add() {
		editing = null;
		editorOpen = true;
	}

	function edit(observation: Observation) {
		editing = observation;
		editorOpen = true;
	}

	function askDelete(observation: Observation) {
		toDelete = observation;
		confirmOpen = true;
	}

	async function confirmDelete() {
		if (!toDelete) return;
		deleting = true;
		try {
			await deleteObservation(toDelete.id);
			ondeleted(toDelete.id);
			confirmOpen = false;
		} catch (e) {
			toast.error(workbenchError(e, 'Could not delete the report.'));
		} finally {
			deleting = false;
		}
	}
</script>

<div class="space-y-2">
	{#if observations.length > 0}
		<ul class="space-y-2">
			{#each observations as obs (obs.id)}
				{@const href = locationHref(obs.location)}
				<li class="rounded-md border border-border bg-surface-1 px-3 py-2.5">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0 space-y-1">
							<p class="break-words text-sm font-medium leading-snug">{obs.title}</p>
							<div class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
								{#if obs.severity}
									<span class={cn('inline-flex items-center rounded border px-1.5 py-px font-medium', severityColors[obs.severity])}>
										Your severity: {severityLabel(obs.severity)}
									</span>
								{/if}
								{#if obs.category}<span>{categoryLabel(obs.kind, obs.category)}</span>{/if}
								{#if obs.cwe}<span class="font-mono">{obs.cwe}</span>{/if}
								{#if obs.checklist_item_id}<span>· {checklistLabel(obs.checklist_item_id)}</span>{/if}
							</div>
							{#if obs.location}
								<p class="min-w-0 truncate font-mono text-xs">
									{#if href}
										<a {href} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
											{obs.location}<ExternalLink class="size-3 shrink-0" aria-hidden="true" />
										</a>
									{:else}
										<span class="text-muted-foreground">{obs.location}</span>
									{/if}
								</p>
							{/if}
							{#if obs.description}
								<p class="line-clamp-4 whitespace-pre-wrap break-words text-xs text-muted-foreground">{obs.description}</p>
							{/if}
						</div>
						{#if !readOnly}
							<div class="flex shrink-0 items-center">
								<Button variant="ghost" size="icon" class="size-8" aria-label="Edit “{obs.title}”" onclick={() => edit(obs)}>
									<Pencil class="size-3.5" />
								</Button>
								<Button variant="ghost" size="icon" class="size-8" aria-label="Delete “{obs.title}”" onclick={() => askDelete(obs)}>
									<Trash2 class="size-3.5" />
								</Button>
							</div>
						{/if}
					</div>
					{#if obs.attachments.length > 0}
						<ul class="mt-2 flex flex-wrap gap-1.5">
							{#each obs.attachments as att, i (att.id)}
								<li>
									<a
										href={portalAttachmentUrl(att.id)}
										target="_blank"
										rel="noopener noreferrer"
										class="block overflow-hidden rounded border border-border"
										title="Open screenshot {i + 1}"
									>
										<img src={portalAttachmentUrl(att.id)} alt="Screenshot {i + 1} of “{obs.title}”" class="h-14 w-20 object-cover" loading="lazy" />
									</a>
								</li>
							{/each}
						</ul>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
	{#if !readOnly}
		<Button variant="outline" size="sm" onclick={add}>
			<Plus />
			{addLabel ?? `Report a ${KIND_LABELS[kind].toLowerCase()}`}
		</Button>
	{/if}
</div>

{#if !readOnly}
	<ObservationEditor
		bind:open={editorOpen}
		{assignmentId}
		{kind}
		{slot}
		{blockId}
		{appLabel}
		observation={editing}
		{checklist}
		{onsaved}
	/>
	<ConfirmDialog
		bind:open={confirmOpen}
		title="Delete this report?"
		description={toDelete ? `“${toDelete.title}” and its screenshots will be removed.` : ''}
		confirmLabel="Delete"
		destructive
		busy={deleting}
		onConfirm={confirmDelete}
	/>
{/if}
