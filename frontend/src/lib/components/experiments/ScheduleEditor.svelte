<script lang="ts">
	/**
	 * ScheduleEditor — one cron schedule of an experiment: the action (top up
	 * failed cells, add replicates, clone and run), when (Nightly · Weekly ·
	 * Custom cron, in a timezone), "skip if still running", and the next five
	 * runs from the server (`cronPreview`, debounced), which also validates
	 * the expression.
	 *
	 * Props:
	 * - `value` (bindable `ScheduleSpec`): replaced, never mutated.
	 * - `invalid` (bindable output): the expression is not a valid cron.
	 * - `idPrefix`, `disabled`.
	 */
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Select from '$lib/components/ui/select';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import { cronPreview } from '$lib/api/experiments';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import type { ScheduleAction, ScheduleSpec } from '$lib/api/types/experiments';
	import {
		CRON_PRESETS,
		CUSTOM_PRESET,
		SCHEDULE_ACTION_DESCRIPTIONS,
		SCHEDULE_ACTION_LABELS,
		describeCron,
		looksLikeCron,
		normalizeCron,
		presetForCron,
	} from './cron';

	interface Props {
		value: ScheduleSpec;
		invalid?: boolean;
		idPrefix?: string;
		disabled?: boolean;
	}

	let { value = $bindable(), invalid = $bindable(false), idPrefix = 'schedule', disabled = false }: Props = $props();

	const PREVIEW_DEBOUNCE_MS = 400;

	// "Custom" stays selected while typing even if the text matches a preset.
	let presetChoice = $state(presetForCron(value.cron));

	let preview = $state<{ cron: string; tz: string; valid: boolean; detail: string; next: string[] } | null>(null);
	let previewError = $state('');

	const actionItems = (Object.keys(SCHEDULE_ACTION_LABELS) as ScheduleAction[]).map((a) => ({
		value: a,
		label: SCHEDULE_ACTION_LABELS[a],
		description: SCHEDULE_ACTION_DESCRIPTIONS[a],
	}));
	const presetItems = [
		...CRON_PRESETS.map((p) => ({ value: p.value, label: p.label })),
		{ value: CUSTOM_PRESET, label: 'Custom (cron)' },
	];
	const timezones = (() => {
		try {
			return (Intl as unknown as { supportedValuesOf(k: string): string[] }).supportedValuesOf('timeZone');
		} catch {
			return ['UTC'];
		}
	})();
	const timezoneItems = $derived(
		[...new Set([value.timezone, 'UTC', ...timezones])].filter(Boolean).map((tz) => ({ value: tz, label: tz }))
	);

	function patch(next: Partial<ScheduleSpec>) {
		value = { ...value, ...next };
	}

	function pickPreset(choice: string) {
		presetChoice = choice;
		const preset = CRON_PRESETS.find((p) => p.value === choice);
		if (preset) patch({ cron: preset.cron });
	}

	const shapeOk = $derived(looksLikeCron(value.cron));

	$effect(() => {
		const cron = normalizeCron(value.cron);
		const tz = value.timezone;
		if (!looksLikeCron(cron)) {
			preview = null;
			return;
		}
		const controller = new AbortController();
		const timer = setTimeout(() => {
			cronPreview(cron, tz, { signal: controller.signal })
				.then((r) => {
					preview = { cron, tz, valid: r.valid, detail: r.detail, next: r.next_runs };
					previewError = '';
				})
				.catch((e: unknown) => {
					if (!isAbortError(e)) previewError = formatApiError(e, 'Could not check the schedule.');
				});
		}, PREVIEW_DEBOUNCE_MS);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	const current = $derived(
		preview && preview.cron === normalizeCron(value.cron) && preview.tz === value.timezone ? preview : null
	);

	$effect(() => {
		invalid = !shapeOk || (current != null && !current.valid);
	});

	function when(iso: string): string {
		try {
			return new Date(iso).toLocaleString(undefined, {
				weekday: 'short',
				day: 'numeric',
				month: 'short',
				hour: '2-digit',
				minute: '2-digit',
				timeZone: value.timezone || undefined,
			});
		} catch {
			return iso;
		}
	}
</script>

<div class="space-y-3">
	<div class="grid gap-3 sm:grid-cols-2">
		<div class="space-y-1.5">
			<Label class="text-xs" for="{idPrefix}-action">Action</Label>
			<Select.Simple
				id="{idPrefix}-action"
				size="sm"
				value={value.action}
				items={actionItems}
				{disabled}
				onValueChange={(v) => patch({ action: v as ScheduleAction })}
			/>
			<p class="text-xs text-muted-foreground">{SCHEDULE_ACTION_DESCRIPTIONS[value.action]}</p>
		</div>
		{#if value.action === 'add_replicates'}
			<NumberField
				id="{idPrefix}-replicates"
				label="Repeats added per run"
				value={value.params?.replicates ?? 1}
				min={1}
				max={5}
				required
				{disabled}
				onchange={(n) => patch({ params: { ...(value.params ?? {}), replicates: n ?? 1 } })}
			/>
		{:else if value.action === 'clone_and_run'}
			<div class="space-y-1.5">
				<Label class="text-xs" for="{idPrefix}-name">Clone name</Label>
				<Input
					id="{idPrefix}-name"
					class="h-8 text-sm"
					value={value.params?.name_template ?? ''}
					placeholder={'{name} {date}'}
					{disabled}
					oninput={(e: Event) => patch({ params: { ...(value.params ?? {}), name_template: (e.currentTarget as HTMLInputElement).value } })}
				/>
				<p class="text-xs text-muted-foreground">Placeholders: <span class="font-mono">{'{name}'}</span>, <span class="font-mono">{'{date}'}</span>.</p>
			</div>
		{/if}
	</div>

	<div class="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
		<div class="space-y-1.5">
			<Label class="text-xs" for="{idPrefix}-when">When</Label>
			<Select.Simple id="{idPrefix}-when" size="sm" value={presetChoice} items={presetItems} {disabled} onValueChange={pickPreset} />
		</div>
		<div class="space-y-1.5">
			<Label class="text-xs" for="{idPrefix}-tz">Timezone</Label>
			<Select.Simple
				id="{idPrefix}-tz"
				size="sm"
				value={value.timezone}
				items={timezoneItems}
				{disabled}
				onValueChange={(v) => patch({ timezone: v })}
			/>
		</div>
	</div>
	{#if presetChoice === CUSTOM_PRESET}
		<div class="space-y-1.5">
			<Label class="text-xs" for="{idPrefix}-cron">Cron expression</Label>
			<Input
				id="{idPrefix}-cron"
				class="h-8 font-mono text-sm"
				value={value.cron}
				placeholder="0 2 * * *"
				aria-invalid={invalid ? true : undefined}
				aria-describedby="{idPrefix}-cron-note"
				{disabled}
				oninput={(e: Event) => patch({ cron: (e.currentTarget as HTMLInputElement).value })}
			/>
			<p id="{idPrefix}-cron-note" class="text-xs {invalid ? 'text-danger-fg' : 'text-muted-foreground'}">
				{#if !shapeOk}
					Five fields: minute hour day-of-month month day-of-week.
				{:else if current && !current.valid}
					{current.detail || 'Not a valid cron expression.'}
				{:else}
					{describeCron(value.cron)}
				{/if}
			</p>
		</div>
	{/if}

	<label class="flex items-center gap-2 text-xs">
		<Checkbox checked={value.skip_if_active} {disabled} onCheckedChange={(v) => patch({ skip_if_active: v === true })} />
		Skip a run while the experiment still has queued or running cells
	</label>

	<div class="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs" aria-live="polite">
		<p class="section-label">Next runs</p>
		{#if previewError}
			<p class="text-danger-fg">{previewError}</p>
		{:else if current?.valid}
			<ul class="mt-1 grid gap-0.5 font-mono text-xs tabular-nums sm:grid-cols-2">
				{#each current.next as run (run)}<li>{when(run)}</li>{/each}
			</ul>
		{:else if shapeOk && !current}
			<p class="loading-mono">checking</p>
		{:else}
			<p class="text-muted-foreground">—</p>
		{/if}
	</div>
</div>
