<script lang="ts">
	/**
	 * GuidanceEditor — `GuidanceContent`: the security hint level (none / generic / CWE-specific), where
	 * the CWE list comes from (the task's surfaces or an explicit list), library prescriptions and the
	 * fragment template (none for "none").
	 *
	 * Props: `value`, `onchange`, `readonly`.
	 */
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import { Button } from '$lib/components/ui/button';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import type { GuidanceContent } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';
	import LibraryRefPicker from './LibraryRefPicker.svelte';

	interface Props {
		value: GuidanceContent;
		onchange: (v: GuidanceContent) => void;
		readonly?: boolean;
	}

	let { value, onchange, readonly = false }: Props = $props();

	const set = (patch: Partial<GuidanceContent>) => onchange({ ...value, ...patch });
	const setPrescription = (i: number, patch: Partial<GuidanceContent['library_prescriptions'][number]>) =>
		set({ library_prescriptions: value.library_prescriptions.map((p, j) => (j === i ? { ...p, ...patch } : p)) });
</script>

<div class="space-y-5">
	<FormRow label="Security hint" inline help="A study factor: compare no guidance, generic advice and CWE-specific advice.">
		<Segmented
			aria-label="Security hint"
			value={value.security_hint}
			disabled={readonly}
			items={[
				{ value: 'none', label: 'None' },
				{ value: 'generic', label: 'Generic' },
				{ value: 'cwe_specific', label: 'CWE-specific' },
			]}
			onValueChange={(v) => set({ security_hint: v as GuidanceContent['security_hint'], fragment: v === 'none' ? null : value.fragment })}
		/>
	</FormRow>

	{#if value.security_hint === 'cwe_specific'}
		<FormRow label="CWE source" inline help="Task surfaces: the CWEs the task declares. Explicit: the list below for every task.">
			<Segmented aria-label="CWE source" value={value.cwe_source} disabled={readonly} items={[{ value: 'task_surfaces', label: 'Task surfaces' }, { value: 'explicit', label: 'Explicit list' }]} onValueChange={(v) => set({ cwe_source: v as GuidanceContent['cwe_source'] })} />
		</FormRow>
		{#if value.cwe_source === 'explicit'}
			<FormRow label="CWEs" inline>
				<ListInput value={value.cwes} {readonly} placeholder="cwe-89, cwe-79" onchange={(v) => set({ cwes: v })} />
			</FormRow>
		{/if}
	{/if}

	{#if value.security_hint !== 'none'}
		<FormRow label="Fragment" inline help="The prompt template (role fragment) rendered as guidance.fragment_text.">
			<LibraryRefPicker kind="prompt" value={value.fragment ? [value.fragment] : []} filter={(i) => (i.content as { role?: string }).role === 'fragment'} disabled={readonly} onchange={(r) => set({ fragment: r[0] ?? null })} />
		</FormRow>
	{/if}

	<section class="space-y-2" aria-labelledby="guidance-prescriptions">
		<h3 id="guidance-prescriptions" class="section-label">Library prescriptions</h3>
		<p class="text-xs text-muted-foreground">"Use library X for purpose Y" (for example password hashing → bcrypt), rendered as guidance.prescriptions.</p>
		{#each value.library_prescriptions as p, i (i)}
			<div class="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
				<Input value={p.purpose} oninput={(e) => setPrescription(i, { purpose: e.currentTarget.value })} placeholder="password hashing" class="h-8" aria-label="Purpose" readonly={readonly} />
				<Input value={p.library} oninput={(e) => setPrescription(i, { library: e.currentTarget.value })} placeholder="bcrypt" class="h-8 font-mono" aria-label="Library" readonly={readonly} />
				{#if !readonly}
					<Button variant="ghost" size="icon" aria-label="Remove prescription" onclick={() => set({ library_prescriptions: value.library_prescriptions.filter((_, j) => j !== i) })}><Trash2 /></Button>
				{/if}
			</div>
		{/each}
		{#if !readonly}
			<Button variant="outline" size="sm" onclick={() => set({ library_prescriptions: [...value.library_prescriptions, { purpose: '', library: '' }] })}><Plus />Add prescription</Button>
		{/if}
	</section>
</div>
