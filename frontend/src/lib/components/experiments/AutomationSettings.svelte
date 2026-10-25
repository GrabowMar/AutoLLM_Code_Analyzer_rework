<script lang="ts">
	/**
	 * AutomationSettings — the composer's "Automation" section: post-run steps
	 * (PostRunEditor) and one optional cron schedule (ScheduleEditor). More
	 * schedules can be added later on the experiment's Automation tab.
	 *
	 * Props:
	 * - `postRun` (bindable), `schedule` (bindable; null = none), `mode`.
	 * - `scheduleInvalid` (bindable output).
	 */
	import { Switch } from '$lib/components/ui/switch';
	import type { PostRunSpec, ScheduleSpec } from '$lib/api/types/experiments';
	import PostRunEditor from './PostRunEditor.svelte';
	import ScheduleEditor from './ScheduleEditor.svelte';
	import { defaultSchedule } from './cron';

	interface Props {
		postRun: PostRunSpec;
		schedule: ScheduleSpec | null;
		mode: 'agent' | 'scaffolding';
		scheduleInvalid?: boolean;
	}

	let { postRun = $bindable(), schedule = $bindable(), mode, scheduleInvalid = $bindable(false) }: Props = $props();

	let scheduleValue = $state<ScheduleSpec>(schedule ?? defaultSchedule());
	let editorInvalid = $state(false);

	$effect(() => {
		if (schedule) schedule = scheduleValue;
	});
	$effect(() => {
		scheduleInvalid = !!schedule && editorInvalid;
	});
</script>

<div class="space-y-5">
	<div class="space-y-2">
		<p class="section-label">After the run</p>
		<PostRunEditor bind:value={postRun} {mode} idPrefix="composer-post" />
	</div>
	<div class="space-y-3 border-t border-border/60 pt-4">
		<div class="flex items-start gap-3">
			<Switch
				id="composer-schedule"
				checked={schedule != null}
				describedBy="composer-schedule-note"
				onCheckedChange={(on) => (schedule = on ? scheduleValue : null)}
			/>
			<div class="min-w-0 space-y-0.5">
				<label for="composer-schedule" class="text-sm font-medium">Schedule</label>
				<p id="composer-schedule-note" class="text-[11px] text-muted-foreground">
					Re-run failed cells nightly, add replicates, or launch a fresh clone on a cron schedule.
				</p>
			</div>
		</div>
		{#if schedule}
			<ScheduleEditor bind:value={scheduleValue} bind:invalid={editorInvalid} idPrefix="composer-schedule" />
		{/if}
	</div>
</div>
