<script lang="ts">
	/**
	 * CapacityCard — the executor and its slot lanes (/endpoints page).
	 *
	 * Everyone sees the executor status (online, stale code, draining with what
	 * remains) and sandbox / evaluation / LLM-job / post-run slot usage. Staff
	 * also edit the slot counts (`PATCH /execution/settings/`; a 400 shows the
	 * memory arithmetic inline) and toggle Drain, which reports "safe to
	 * restart" once every lane is idle.
	 *
	 * Props:
	 * - `capacity`: `Capacity` (null while loading).
	 * - `isStaff`.
	 * - `onchanged()`: after settings were saved (refetch capacity).
	 */
	import { toast } from 'svelte-sonner';
	import { getExecutorSettings, memoryRuleError, updateExecutorSettings } from '$lib/api/execution';
	import type { Capacity, ExecutorSettings, MemoryRuleError } from '$lib/api/types/execution';
	import { formatApiError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import { toneClasses } from '$lib/constants/colors';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import Switch from '$lib/components/ui/switch/switch.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import SlotMeter from './SlotMeter.svelte';
	import { formatAgo } from './endpoint-display';
	import Cpu from '@lucide/svelte/icons/cpu';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		capacity: Capacity | null;
		isStaff?: boolean;
		now?: number;
		onchanged?: () => void;
	}

	let { capacity, isStaff = false, now = Date.now(), onchanged }: Props = $props();

	type SlotKey = 'sandbox_slots' | 'eval_slots' | 'llm_job_threads' | 'post_run_slots';

	let settings = $state<ExecutorSettings | null>(null);
	let draft = $state<Record<SlotKey, number | null>>({
		sandbox_slots: null,
		eval_slots: null,
		llm_job_threads: null,
		post_run_slots: null,
	});
	let invalid = $state<Record<string, boolean>>({});
	let saving = $state(false);
	let drainBusy = $state(false);
	let ruleError = $state<MemoryRuleError | null>(null);
	let settingsError = $state('');

	$effect(() => {
		if (!isStaff) return;
		getExecutorSettings()
			.then((s) => {
				settings = s;
				resetDraft(s);
			})
			.catch((e) => (settingsError = formatApiError(e, 'Could not load executor settings.')));
	});

	function resetDraft(s: ExecutorSettings) {
		draft = {
			sandbox_slots: s.sandbox_slots,
			eval_slots: s.eval_slots,
			llm_job_threads: s.llm_job_threads,
			post_run_slots: s.post_run_slots,
		};
	}

	const executor = $derived(capacity?.executor ?? null);
	const executorState = $derived.by((): { label: string; tone: 'success' | 'warning' | 'danger' } => {
		if (!executor?.online) return { label: 'Executor offline', tone: 'danger' };
		if (executor.draining) return { label: 'Draining', tone: 'warning' };
		if (executor.code_stale) return { label: 'Running old code', tone: 'warning' };
		return { label: 'Executor online', tone: 'success' };
	});
	const lanesIdle = $derived(
		!!capacity &&
			capacity.lanes.sandbox.used === 0 &&
			capacity.lanes.eval.used === 0 &&
			capacity.lanes.llm.used === 0 &&
			capacity.lanes.post.used === 0
	);
	const dirty = $derived(
		!!settings && (Object.keys(draft) as SlotKey[]).some((k) => draft[k] != null && draft[k] !== settings![k])
	);
	const draftInvalid = $derived(
		Object.values(invalid).some(Boolean) || (Object.values(draft) as (number | null)[]).some((v) => v == null)
	);
	const sandboxOverLimit = $derived(
		!!capacity &&
			draft.sandbox_slots != null &&
			capacity.limits.max_sandbox_slots_allowed > 0 &&
			draft.sandbox_slots > capacity.limits.max_sandbox_slots_allowed
	);

	const lanes = $derived(
		capacity
			? [
					{ key: 'sandbox', label: 'Sandboxes', value: capacity.lanes.sandbox },
					{ key: 'eval', label: 'Evaluation', value: capacity.lanes.eval },
					{ key: 'llm', label: 'LLM jobs', value: capacity.lanes.llm },
					{ key: 'post', label: 'Post-run', value: capacity.lanes.post },
				]
			: []
	);

	async function save() {
		if (!settings || !dirty || draftInvalid) return;
		saving = true;
		ruleError = null;
		try {
			const patch: Partial<ExecutorSettings> = {};
			for (const k of Object.keys(draft) as SlotKey[]) {
				if (draft[k] != null && draft[k] !== settings[k]) patch[k] = draft[k]!;
			}
			settings = await updateExecutorSettings(patch);
			resetDraft(settings);
			toast.success('Executor settings saved');
			onchanged?.();
		} catch (e) {
			ruleError = memoryRuleError(e);
			if (!ruleError) toast.error(formatApiError(e, 'Could not save the settings.'));
		} finally {
			saving = false;
		}
	}

	async function setDraining(next: boolean) {
		if (!settings) return;
		drainBusy = true;
		try {
			settings = await updateExecutorSettings({ draining: next });
			toast.success(next ? 'Draining: no new runs start' : 'Drain off: runs start again');
			onchanged?.();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not change drain mode.'));
		} finally {
			drainBusy = false;
		}
	}

	const slotFields: { key: SlotKey; label: string; hint: string }[] = [
		{ key: 'sandbox_slots', label: 'Sandbox slots', hint: 'Agent runs at once (each needs apps-dind memory).' },
		{ key: 'eval_slots', label: 'Evaluation slots', hint: 'Test containers at once.' },
		{ key: 'llm_job_threads', label: 'LLM job threads', hint: 'Scaffolding runs at once.' },
		{ key: 'post_run_slots', label: 'Post-run slots', hint: 'Analyses and reports at once.' },
	];
</script>

<Card.Root>
	<Card.Header class="pb-3">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<Card.Title class="flex items-center gap-2 text-base">
				<Cpu class="size-4 text-muted-foreground" aria-hidden="true" />Capacity
			</Card.Title>
			{#if capacity}
				<span
					class={cn('inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium', toneClasses[executorState.tone])}
					title={executor?.leader
						? `Executor ${executor.leader.code_version} · last heartbeat ${formatAgo(executor.leader.last_seen_at, now)}`
						: 'No executor heartbeat'}
				>
					<span class={cn('status-dot h-1.5 w-1.5', executorState.tone === 'success' && 'status-dot-live')} aria-hidden="true"></span>
					{executorState.label}
				</span>
			{/if}
		</div>
		<Card.Description>Shared execution slots for every user's runs.</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-4">
		{#if !capacity}
			<LoadingState label="loading capacity" class="py-6" />
		{:else}
			{#if executor?.draining}
				<p class="text-xs text-warning-fg" role="status">
					{#if lanesIdle && executor.draining_remaining.jobs === 0 && executor.draining_remaining.post_runs === 0}
						Drained: safe to restart the executor.
					{:else}
						Draining: {executor.draining_remaining.jobs} runs and {executor.draining_remaining.post_runs} post-run steps
						still finishing. No new runs start.
					{/if}
				</p>
			{:else if executor?.code_stale}
				<p class="text-xs text-warning-fg" role="status">
					The executor runs older code than the web app. Drain it and restart it to pick up the change.
				</p>
			{:else if !executor?.online}
				<p class="text-xs text-danger-fg" role="status">No executor heartbeat: queued runs wait until it is back.</p>
			{/if}

			<div class="grid gap-x-6 gap-y-2 sm:grid-cols-2">
				{#each lanes as lane (lane.key)}
					<SlotMeter label={lane.label} used={lane.value.used} total={lane.value.total} class="justify-between" />
				{/each}
			</div>

			{#each capacity.limits.warnings as warning (warning)}
				<p class="flex items-start gap-1.5 text-xs text-warning-fg">
					<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />{warning}
				</p>
			{/each}

			{#if isStaff}
				<div class="space-y-3 border-t border-border pt-4">
					{#if settingsError}
						<p class="text-xs text-danger-fg">{settingsError}</p>
					{:else if !settings}
						<LoadingState label="loading settings" class="py-4" />
					{:else}
						<div class="grid gap-3 sm:grid-cols-2">
							{#each slotFields as field (field.key)}
								<NumberField
									id="exec-{field.key}"
									label={field.label}
									min={0}
									max={field.key === 'sandbox_slots' ? 64 : 256}
									required
									hint={field.hint}
									bind:value={() => draft[field.key], (v) => (draft = { ...draft, [field.key]: v })}
									bind:invalid={() => !!invalid[field.key], (v) => {
										if (!!invalid[field.key] !== v) invalid = { ...invalid, [field.key]: v };
									}}
								/>
							{/each}
						</div>
						<p class={cn('text-[11px]', sandboxOverLimit ? 'text-warning-fg' : 'text-muted-foreground')}>
							{#if capacity.limits.max_sandbox_slots_allowed > 0}
								apps-dind memory {capacity.limits.apps_dind_mem || 'unknown'} allows at most
								{capacity.limits.max_sandbox_slots_allowed} sandbox slots (3 GB per sandbox plus gateway, evaluation and
								analyzer headroom).
							{:else}
								apps-dind memory {capacity.limits.apps_dind_mem || 'unknown'} fits one sandbox only when evaluation
								is idle: 1 sandbox slot runs, and sandbox runs and evaluations take turns (3 GB per sandbox plus
								gateway, evaluation and analyzer headroom). Raise the apps-dind memory to run them side by side.
							{/if}
						</p>
						{#if ruleError}
							<div class={cn('rounded-md border px-3 py-2 text-xs', toneClasses.danger)} role="alert">
								<p class="font-medium">{ruleError.detail || 'Not enough memory for these slots.'}</p>
								<p class="mt-1 font-mono">{ruleError.arithmetic}</p>
							</div>
						{/if}
						<div class="flex flex-wrap items-center justify-between gap-3">
							<label class="flex items-center gap-2 text-sm">
								<Switch
									checked={settings.draining}
									disabled={drainBusy}
									label="Drain the executor"
									onCheckedChange={(v) => setDraining(v)}
								/>
								Drain
								<span class="text-xs text-muted-foreground">(finish running work, start nothing new)</span>
							</label>
							<div class="flex gap-2">
								{#if dirty}
									<Button type="button" variant="ghost" size="sm" onclick={() => settings && resetDraft(settings)} disabled={saving}>
										Reset
									</Button>
								{/if}
								<Button type="button" size="sm" onclick={save} disabled={!dirty || draftInvalid || saving}>
									{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}Save
								</Button>
							</div>
						</div>
					{/if}
				</div>
			{/if}
		{/if}
	</Card.Content>
</Card.Root>
