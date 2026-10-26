<script lang="ts">
	/**
	 * SchedulesCard — an experiment's cron schedules: enable switch, action,
	 * when (in words), next run, last result (incl. digest-drift errors), Run
	 * now, edit (ScheduleEditor in a dialog) and delete.
	 *
	 * Props: `experimentId`, `schedules`, `onchange()` (refetch after a write),
	 * `onlaunched()` (a "Run now" queued runs on this experiment: the page
	 * reloads it and resumes live updates, since a top-up reopens a finished
	 * experiment), `readonly` (archived: no new schedules, edits or runs).
	 */
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Switch } from '$lib/components/ui/switch';
	import { toneClasses } from '$lib/constants/colors';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import {
		createSchedule,
		deleteSchedule,
		runScheduleNow,
		updateSchedule,
		type ScheduleOut,
		type ScheduleSpec,
	} from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import CalendarClock from '@lucide/svelte/icons/calendar-clock';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Play from '@lucide/svelte/icons/play';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ScheduleEditor from './ScheduleEditor.svelte';
	import { SCHEDULE_ACTION_LABELS, defaultSchedule, describeCron } from './cron';
	import { timeAgo, timeUntil } from './format';

	interface Props {
		experimentId: string;
		schedules: ScheduleOut[];
		onchange: () => void;
		onlaunched?: () => void;
		readonly?: boolean;
	}

	let { experimentId, schedules, onchange, onlaunched, readonly = false }: Props = $props();

	let busy = $state<number | 'new' | null>(null);
	let editorOpen = $state(false);
	let editing = $state<ScheduleOut | null>(null);
	let editorValue = $state<ScheduleSpec>(defaultSchedule());
	let editorInvalid = $state(false);
	let editorError = $state('');
	let deleteTarget = $state<ScheduleOut | null>(null);
	let deleteOpen = $state(false);

	function openEditor(schedule?: ScheduleOut) {
		editing = schedule ?? null;
		editorValue = schedule
			? {
					action: schedule.action,
					cron: schedule.cron,
					timezone: schedule.timezone,
					params: { ...(schedule.params ?? {}) },
					skip_if_active: schedule.skip_if_active,
					enabled: schedule.enabled,
				}
			: defaultSchedule();
		editorError = '';
		editorOpen = true;
	}

	async function saveEditor() {
		busy = editing?.id ?? 'new';
		editorError = '';
		try {
			if (editing) await updateSchedule(experimentId, editing.id, $state.snapshot(editorValue));
			else await createSchedule(experimentId, $state.snapshot(editorValue));
			editorOpen = false;
			toast.success(editing ? 'Schedule updated' : 'Schedule added');
			onchange();
		} catch (e) {
			editorError = formatApiError(e, 'Could not save the schedule.');
		} finally {
			busy = null;
		}
	}

	async function toggle(schedule: ScheduleOut, enabled: boolean) {
		busy = schedule.id;
		try {
			await updateSchedule(experimentId, schedule.id, { enabled });
			onchange();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not update the schedule.'));
		} finally {
			busy = null;
		}
	}

	async function runNow(schedule: ScheduleOut) {
		busy = schedule.id;
		try {
			const r = await runScheduleNow(experimentId, schedule.id);
			// The action records failures (e.g. digest drift) in `last_result` and
			// answers 200 with outcome "error"; the refreshed row shows the detail.
			if (r.outcome === 'error') toast.error('The schedule could not run; see its last result.');
			else if (r.outcome === 'skipped') toast.info('Skipped: the experiment still has queued or running cells.');
			else toast.success(`Started: ${r.jobs_created} run${r.jobs_created === 1 ? '' : 's'} queued`);
			onchange();
			// A clone runs elsewhere; a top-up / replicates run here (and may reopen it).
			const here = !r.experiment_id || r.experiment_id === experimentId;
			if (r.outcome !== 'error' && r.outcome !== 'skipped' && r.jobs_created > 0 && here) onlaunched?.();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not run the schedule.'));
		} finally {
			busy = null;
		}
	}

	async function confirmDelete() {
		const target = deleteTarget;
		if (!target) return;
		busy = target.id;
		try {
			await deleteSchedule(experimentId, target.id);
			deleteOpen = false;
			onchange();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not delete the schedule.'));
		} finally {
			busy = null;
		}
	}

	function resultText(s: ScheduleOut): { text: string; tone: 'success' | 'warning' | 'danger' | 'neutral' } | null {
		const r = s.last_result;
		if (!r?.outcome) return null;
		if (r.outcome === 'error') {
			return {
				text: r.detail === 'digest_drift' ? 'Blocked: a model’s weights changed (digest drift)' : `Error: ${r.detail ?? 'unknown'}`,
				tone: 'danger',
			};
		}
		if (r.outcome === 'skipped') return { text: `Skipped${r.detail ? ` (${r.detail})` : ''}`, tone: 'neutral' };
		return { text: `Fired · ${r.jobs_created ?? 0} runs`, tone: 'success' };
	}
</script>

<Card.Root>
	<Card.Header class="flex-row items-start justify-between gap-2">
		<div class="space-y-1">
			<Card.Title>Schedules</Card.Title>
			<Card.Description>Top up failed cells, add replicates or launch a fresh clone on a cron schedule.</Card.Description>
		</div>
		{#if !readonly}
			<Button variant="outline" size="sm" onclick={() => openEditor()}><Plus />Add schedule</Button>
		{/if}
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if readonly}
			<p class="text-xs text-muted-foreground">This experiment is archived: its schedules no longer run and cannot be changed. Clone it to schedule again.</p>
		{/if}
		{#if schedules.length === 0}
			<EmptyState compact icon={CalendarClock} title="No schedules" description="Add one to re-run failed cells nightly or replicate weekly." />
		{:else}
			<div class="overflow-x-auto rounded-md border border-border">
				<table class="table-card-mobile">
					<thead>
						<tr>
							<th scope="col">On</th>
							<th scope="col">Action</th>
							<th scope="col">When</th>
							<th scope="col">Next run</th>
							<th scope="col">Last result</th>
							<th scope="col"><span class="sr-only">Actions</span></th>
						</tr>
					</thead>
					<tbody>
						{#each schedules as s (s.id)}
							{@const result = resultText(s)}
							<tr>
								<td data-label="On">
									<Switch size="sm" checked={s.enabled} disabled={readonly || busy === s.id} label="Enabled" onCheckedChange={(v) => toggle(s, v)} />
								</td>
								<td data-label="Action">
									{SCHEDULE_ACTION_LABELS[s.action]}
									{#if s.action === 'add_replicates' && s.params?.replicates}<span class="text-muted-foreground"> · +{s.params.replicates}</span>{/if}
								</td>
								<td data-label="When">
									<span title="{s.cron} ({s.timezone})">{describeCron(s.cron)}</span>
									<span class="cell-sub block text-xs text-muted-foreground">{s.timezone}{s.skip_if_active ? ' · skips while running' : ''}</span>
								</td>
								<td data-label="Next run" class="whitespace-nowrap tabular-nums" title={s.next_run_at ? new Date(s.next_run_at).toLocaleString() : ''}>
									{#if s.enabled}{timeUntil(s.next_run_at)}{:else}<span class="text-muted-foreground">paused</span>{/if}
								</td>
								<td data-label="Last result">
									{#if result}
										<span class="inline-block rounded-full border px-2 py-0.5 text-xs {toneClasses[result.tone]}">{result.text}</span>
										<span class="cell-sub mt-0.5 block text-xs text-muted-foreground">{timeAgo(s.last_run_at)}</span>
										{#if s.last_result.experiment_id && s.last_result.experiment_id !== experimentId}
											<a href="/experiments/{s.last_result.experiment_id}" class="text-xs text-success-fg hover:underline">Open the clone</a>
										{/if}
									{:else}
										<span class="text-muted-foreground">never ran</span>
									{/if}
								</td>
								<td data-label="" class="w-px whitespace-nowrap">
									<span class="flex items-center justify-end gap-0.5">
										<span class="row-actions-more flex items-center gap-0.5">
											{#if !readonly}
												<Button
													variant="ghost"
													size="sm"
													class="size-8 p-0 text-muted-foreground hover:text-foreground"
													aria-label="Edit schedule"
													title="Edit schedule"
													onclick={() => openEditor(s)}
												>
													<Pencil class="size-4" />
												</Button>
											{/if}
											<Button
												variant="ghost"
												size="sm"
												class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
												aria-label="Delete schedule"
												title="Delete schedule"
												onclick={() => {
													deleteTarget = s;
													deleteOpen = true;
												}}
											>
												<Trash2 class="size-4" />
											</Button>
										</span>
										{#if !readonly}
											<Button variant="ghost" size="sm" class="h-7 gap-1 px-2 text-xs" disabled={busy === s.id} onclick={() => runNow(s)}>
												{#if busy === s.id}<LoaderCircle class="size-3 animate-spin motion-reduce:animate-none" />{:else}<Play class="size-3" />{/if}Run now
											</Button>
										{/if}
									</span>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	</Card.Content>
</Card.Root>

<Dialog.Root bind:open={editorOpen}>
	<Dialog.Content class="max-w-xl">
		<Dialog.Header>
			<Dialog.Title>{editing ? 'Edit schedule' : 'Add schedule'}</Dialog.Title>
		</Dialog.Header>
		<ScheduleEditor bind:value={editorValue} bind:invalid={editorInvalid} idPrefix="schedule-dialog" />
		{#if editorError}<p class="text-xs text-danger-fg">{editorError}</p>{/if}
		<Dialog.Footer>
			<Button variant="outline" size="sm" onclick={() => (editorOpen = false)}>Cancel</Button>
			<Button size="sm" disabled={editorInvalid || busy !== null} onclick={saveEditor}>
				{#if busy !== null}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
				{editing ? 'Save' : 'Add schedule'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={deleteOpen}
	title="Delete this schedule?"
	description="It stops firing. Runs it already started are kept."
	confirmLabel="Delete"
	destructive
	busy={busy !== null}
	onConfirm={confirmDelete}
/>
