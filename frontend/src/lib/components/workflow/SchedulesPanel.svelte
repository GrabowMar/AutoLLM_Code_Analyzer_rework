<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Label } from '$lib/components/ui/label';
	import Calendar from '@lucide/svelte/icons/calendar';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
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

	function fmt(s: string | null) {
		return s ? new Date(s).toLocaleString() : '—';
	}

	onMount(load);
</script>

<Card.Root>
	<Card.Header class="flex flex-row items-center justify-between space-y-0">
		<Card.Title class="flex items-center gap-1.5">
			<Calendar class="h-4 w-4" />Schedules ({schedules.length})
		</Card.Title>
		<Button variant="outline" size="sm" onclick={() => (showForm = !showForm)} class="gap-1.5">
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
							class="w-full h-8 rounded-md border bg-background px-2 text-xs"
						>
							{#each CRON_PRESETS as preset (preset.value)}
								<option value={preset.value} selected={preset.value === newCron}>{preset.label}</option>
							{/each}
						</select>
					</div>
					<div class="space-y-1">
						<Label class="text-xs" for="cron-expr">Cron Expression</Label>
						<input
							id="cron-expr"
							type="text"
							bind:value={newCron}
							class="w-full h-8 rounded-md border bg-background px-2 font-mono text-xs"
						/>
					</div>
				</div>
				<label class="flex items-center gap-2 text-xs cursor-pointer">
					<input type="checkbox" bind:checked={newEnabled} class="rounded" />
					Enable immediately
				</label>
				{#if formError}<p class="text-xs text-destructive">{formError}</p>{/if}
				<Button size="sm" onclick={create} disabled={saving} class="w-full">
					{saving ? 'Saving…' : 'Create Schedule'}
				</Button>
			</div>
		{/if}

		{#if loading}
			<div class="flex justify-center py-6"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
		{:else if schedules.length === 0}
			<p class="text-xs text-muted-foreground italic">No schedules. Click <strong>Add</strong> to create one.</p>
		{:else}
			<div class="space-y-1.5">
				{#each schedules as s (s.id)}
					<div class="flex items-center gap-2 rounded-md border bg-card px-3 py-2 text-xs">
						<button
							type="button"
							onclick={() => toggle(s)}
							class="shrink-0 h-5 w-9 rounded-full border transition-colors relative {s.enabled ? 'bg-emerald-500/30 border-emerald-500/50' : 'bg-slate-500/20 border-slate-500/40'}"
							title={s.enabled ? 'Click to disable' : 'Click to enable'}
						>
							<span class="absolute top-0.5 h-3.5 w-3.5 rounded-full transition-transform {s.enabled ? 'translate-x-[18px] bg-emerald-300' : 'translate-x-0.5 bg-slate-400'}"></span>
						</button>
						<div class="flex-1 min-w-0">
							<p class="font-mono">{s.cron_expression}</p>
							<p class="text-[10px] text-muted-foreground">Next: {fmt(s.next_run_at)} · Last: {fmt(s.last_run_at)}</p>
						</div>
						<Badge variant="outline" class="shrink-0 text-[10px] {s.enabled ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-slate-500/10 text-slate-400 border-slate-500/30'}">
							{s.enabled ? 'active' : 'paused'}
						</Badge>
						<Button variant="ghost" size="icon" class="h-7 w-7 text-destructive hover:text-destructive" onclick={() => remove(s.id)}>
							<Trash2 class="h-3.5 w-3.5" />
						</Button>
					</div>
				{/each}
			</div>
		{/if}
	</Card.Content>
</Card.Root>
