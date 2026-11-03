<script lang="ts">
	/**
	 * HarnessPresetEditor — `HarnessPresetContent`: the harness and a verified version (`GET /harnesses/`;
	 * disabled harnesses are listed with their blocked reason, never hidden), the adapter's options
	 * (schema-driven: enum, int, bool, multi_enum; sparse — options equal to the default are dropped), the
	 * controlled-profile flags, command timeout, turn cap and tool policy.
	 *
	 * Props: `value`, `onchange`, `readonly`.
	 */
	import * as Select from '$lib/components/ui/select';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import { formatApiError } from '$lib/api/core';
	import { listHarnesses } from '$lib/api/v3/harnesses';
	import type { HarnessOptionField, HarnessOut, HarnessPresetContent } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';
	import NumberInput from './NumberInput.svelte';
	import { jsonEqual } from './policy';

	interface Props {
		value: HarnessPresetContent;
		onchange: (v: HarnessPresetContent) => void;
		readonly?: boolean;
	}

	let { value, onchange, readonly = false }: Props = $props();

	let harnesses = $state<HarnessOut[]>([]);
	let error = $state('');
	$effect(() => {
		listHarnesses()
			.then((h) => (harnesses = h))
			.catch((e) => (error = formatApiError(e, 'Could not load the harness catalogue.')));
	});

	const harness = $derived(harnesses.find((h) => h.name === value.harness));
	const set = (patch: Partial<HarnessPresetContent>) => onchange({ ...value, ...patch });

	function setOption(field: HarnessOptionField, v: unknown) {
		const options = { ...value.options };
		const fallback = field.default ?? harness?.default_options[field.key];
		if (v === null || v === undefined || v === '' || jsonEqual(v, fallback)) delete options[field.key];
		else options[field.key] = v;
		set({ options });
	}

	const optionValue = (field: HarnessOptionField) => value.options[field.key] ?? field.default ?? harness?.default_options[field.key] ?? null;

	function pickHarness(name: string) {
		const next = harnesses.find((h) => h.name === name);
		set({ harness: name, harness_version: next?.pinned_version ?? value.harness_version, options: {} });
	}
</script>

<div class="space-y-5">
	{#if error}<ErrorAlert message={error} />{/if}
	<div class="grid gap-4 md:grid-cols-2">
		<FormRow label="Harness">
			<Select.Simple
				value={value.harness}
				items={harnesses.length
					? harnesses.map((h) => ({ value: h.name, label: h.label, description: h.enabled ? h.description : `Not enabled: ${h.blocked_reason ?? 'open questions'}` }))
					: [{ value: value.harness, label: value.harness }]}
				disabled={readonly}
				onValueChange={pickHarness}
			/>
			{#if harness && !harness.enabled}
				<p class="text-xs text-warning-fg">Not enabled: {harness.blocked_reason ?? harness.open_questions.join('; ')}. Studies using it are refused at resolve.</p>
			{/if}
		</FormRow>
		<FormRow label="Version" help="Only versions with verified harness facts can run.">
			<Select.Simple
				value={value.harness_version}
				items={(harness?.versions.length ? harness.versions : [value.harness_version]).map((v) => ({ value: v, label: v === harness?.pinned_version ? `${v} (pinned)` : v }))}
				disabled={readonly}
				onValueChange={(v) => set({ harness_version: v })}
			/>
		</FormRow>
	</div>

	{#if harness?.option_schema.length}
		<section class="space-y-3" aria-labelledby="hp-options">
			<h3 id="hp-options" class="section-label">Options</h3>
			<div class="grid gap-4 md:grid-cols-2">
				{#each harness.option_schema as field (field.key)}
					{@const current = optionValue(field)}
					<FormRow label={field.label} help={field.help}>
						{#if field.type === 'bool'}
							<label class="flex items-center gap-2 text-sm"><Checkbox checked={current === true} disabled={readonly} onCheckedChange={(c) => setOption(field, c === true)} />{current ? 'On' : 'Off'}</label>
						{:else if field.type === 'int'}
							<NumberInput value={typeof current === 'number' ? current : null} min={field.min ?? undefined} max={field.max ?? undefined} step={1} {readonly} onchange={(n) => setOption(field, n)} />
						{:else if field.type === 'enum'}
							<Select.Simple value={String(current ?? '')} items={field.choices ?? []} size="sm" disabled={readonly} onValueChange={(v) => setOption(field, v)} />
						{:else if field.type === 'multi_enum'}
							{@const chosen = Array.isArray(current) ? (current as string[]) : []}
							<div class="flex flex-wrap gap-3">
								{#each field.choices ?? [] as c (c.value)}
									<label class="flex items-center gap-1.5 text-sm">
										<Checkbox checked={chosen.includes(c.value)} disabled={readonly} onCheckedChange={(on) => setOption(field, on ? [...chosen, c.value] : chosen.filter((x) => x !== c.value))} />
										{c.label}
									</label>
								{/each}
							</div>
						{:else}
							<ListInput value={Array.isArray(current) ? (current as string[]) : current ? [String(current)] : []} {readonly} onchange={(v) => setOption(field, v.length ? v : null)} />
						{/if}
					</FormRow>
				{/each}
			</div>
		</section>
	{/if}

	<section class="space-y-3" aria-labelledby="hp-profile">
		<h3 id="hp-profile" class="section-label">Profile</h3>
		<div class="grid gap-3 md:grid-cols-3">
			<label class="flex items-start gap-2 text-sm"><Checkbox checked={value.controlled} disabled={readonly} onCheckedChange={(c) => set({ controlled: c === true })} class="mt-0.5" /><span>Controlled <span class="block text-xs text-muted-foreground">Equalised profile across harnesses (section 5.5).</span></span></label>
			<label class="flex items-start gap-2 text-sm"><Checkbox checked={value.pin_system_prompt} disabled={readonly} onCheckedChange={(c) => set({ pin_system_prompt: c === true })} class="mt-0.5" /><span>Pin system prompt <span class="block text-xs text-muted-foreground">Use the captured default prompt of this version.</span></span></label>
			<label class="flex items-start gap-2 text-sm"><Checkbox checked={value.subagents} disabled={readonly} onCheckedChange={(c) => set({ subagents: c === true })} class="mt-0.5" /><span>Subagents <span class="block text-xs text-muted-foreground">Claude Code Task, Goose subagents, OpenCode task.</span></span></label>
		</div>
		<div class="grid gap-4 md:grid-cols-2">
			<FormRow label="Command timeout" help="Seconds per shell command; empty = the Budget preset's time.command_timeout_s.">
				<NumberInput value={value.command_timeout_s} min={1} step={1} placeholder="from budget preset" {readonly} onchange={(n) => set({ command_timeout_s: n })} />
			</FormRow>
			<FormRow label="Max turns" help="Harness-native turn cap; the stricter of this and run.max_turns applies.">
				<NumberInput value={value.max_turns} min={1} step={1} placeholder="from budget preset" {readonly} onchange={(n) => set({ max_turns: n })} />
			</FormRow>
			<FormRow label="Denied tools">
				<ListInput value={value.tool_policy.deny} {readonly} placeholder="none" onchange={(deny) => set({ tool_policy: { ...value.tool_policy, deny } })} />
			</FormRow>
			<FormRow label="Allowed tools only" help="Empty = every tool the harness has (minus denied).">
				<ListInput value={value.tool_policy.allow ?? []} {readonly} placeholder="all" onchange={(allow) => set({ tool_policy: { ...value.tool_policy, allow: allow.length ? allow : null } })} />
			</FormRow>
		</div>
	</section>
</div>
