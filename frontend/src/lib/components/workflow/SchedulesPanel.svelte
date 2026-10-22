<script lang="ts">
	import { formatDateTime as fmt } from '$lib/utils/formatters';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Switch } from '$lib/components/ui/switch';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import Calendar from '@lucide/svelte/icons/calendar';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import {
		listSchedules,
		createSchedule,
		setScheduleEnabled,
		deleteSchedule,
		type ScheduleSummary
	} from '$lib/api/client';

	interface Props {
		pipelineId: string;
	}
	let { pipelineId }: Props = $props();

	const CRON_PRESETS = [
		{ label: 'Every hour', value: '0 * * * *' },
		{ label: 'Daily 9am', value: '0 9 * * *' },
		{ label: 'Daily midnight', value: '0 0 * * *' },
		{ label: 'Weekly Monday 9am', value: '0 9 * * 1' },
		{ label: 'Every 30 min', value: '*/30 * * * *' },
		{ label: 'Every 5 min', value: '*/5 * * * *' }
	];

	let schedules = $state<ScheduleSummary[]>([]);
	let loading = $state(true);
	let showForm = $state(false);
	let newCron = $state('0 * * * *');
	let newEnabled = $state(true);
	let saving = $state(false);
	let formError = $state('');

	async function load() {
		loading = true;
		try {
			const res = await listSchedules();
			schedules = res.items.filter((s) => s.pipeline_id === pipelineId);
		} catch {
			schedules = [];
		} finally {
			loading = false;
		}
	}

	async function create() {
		saving = true;
		formError = '';
		try {
			await createSchedule({ pipeline_id: pipelineId, cron_expression: newCron, enabled: newEnabled });
			showForm = false;
			newCron = '0 * * * *';
			newEnabled = true;
			await load();
		} catch (e: unknown) {
			formError = (e as { detail?: string })?.detail ?? 'Failed to create schedule';
		} finally {
			saving = false;
		}
	}

	async function toggle(s: ScheduleSummary) {
		await setScheduleEnabled(s.id, !s.enabled);
		await load();
	}

	async function remove(id: string) {
		if (!confirm('Delete this schedule?')) return;
		await deleteSchedule(id);
		await load();
	}

	onMount(load);
</script>

<Card.Root>
	<Card.Header class="flex flex-row items-center justify-between space-y-0">
		<div class="space-y-1">
			<Card.Title class="flex items-center gap-1.5">
				<Calendar class="h-4 w-4 text-muted-foreground" aria-hidden="true" />Schedules
			</Card.Title>
			<Card.Description>{schedules.length} cron trigger{schedules.length === 1 ? '' : 's'}</Card.Description>
		</div>
		<Button variant="outline" size="sm" onclick={() => (showForm = !showForm)} aria-expanded={showForm}>
			<Plus class="h-3.5 w-3.5" />{showForm ? 'Cancel' : 'Add'}
		</Button>
	</Card.Header>
	<Card.Content class="space-y-3">
		{#if showForm}
			<div class="rounded-md border bg-muted/30 p-3 space-y-2">
				<div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
					<div class="space-y-1">
						<Label class="text-xs" for="cron-preset">Preset</Label>
						<select
							id="cron-preset"
							onchange={(e) => (newCron = (e.target as HTMLSelectElement).value)}
							class="std-select h-8 w-full text-xs"
						>
							{#each CRON_PRESETS as preset (preset.value)}
								<option value={preset.value} selected={preset.value === newCron}>{preset.label}</option>
							{/each}
						</select>
					</div>
					<div class="space-y-1">
						<Label class="text-xs" for="cron-expr">Cron expression</Label>
						<input
							id="cron-expr"
							type="text"
							bind:value={newCron}
							aria-invalid={formError ? 'true' : undefined}
						class="w-full h-8 rounded-md border border-input bg-background px-2 font-mono text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
						/>
					</div>
				</div>
				<label class="flex items-center gap-2 text-xs cursor-pointer">
					<input type="checkbox" bind:checked={newEnabled} class="rounded accent-primary" />
					Enable immediately
				</label>
				{#if formError}<p class="text-xs text-destructive" role="alert">{formError}</p>{/if}
				<Button size="sm" onclick={create} disabled={saving} class="w-full">
					{saving ? 'Saving…' : 'Create schedule'}
				</Button>
			</div>
		{/if}

		{#if loading}
			<LoadingState class="py-6" label="loading schedules" />
		{:else if schedules.length === 0}
			<EmptyState compact icon={Calendar} title="No schedules" description="Add a cron trigger to run this workflow automatically." />
		{:else}
			<div class="space-y-1.5">
				{#each schedules as s (s.id)}
					<div class="flex items-center gap-2 rounded-md border px-3 py-2 text-xs">
						<Switch
							checked={s.enabled}
							label={s.enabled ? `Disable schedule ${s.cron_expression}` : `Enable schedule ${s.cron_expression}`}
							onCheckedChange={() => toggle(s)}
						/>
						<div class="flex-1 min-w-0">
							<p class="font-mono">{s.cron_expression}</p>
							<p class="text-[11px] text-muted-foreground">Next: {fmt(s.next_run_at)} · Last: {fmt(s.last_run_at)}</p>
						</div>
						<StatusBadge status={s.enabled ? 'active' : 'paused'} />
						<Button variant="ghost" size="icon" class="h-7 w-7 text-destructive hover:text-destructive" aria-label="Delete schedule {s.cron_expression}" onclick={() => remove(s.id)}>
							<Trash2 class="h-3.5 w-3.5" />
						</Button>
					</div>
				{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
