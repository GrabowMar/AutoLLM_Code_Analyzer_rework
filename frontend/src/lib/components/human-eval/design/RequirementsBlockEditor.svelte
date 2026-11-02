<script lang="ts">
	/**
	 * RequirementsBlockEditor — which spec sections become checklist items, the
	 * researcher's own extra items (same for every app) and the note rule.
	 *
	 * Props: `id`, `block` (bindable), `errors` (relative to the block), `readOnly`.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { RequirementSource, RequirementsBlock } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, messagesUnder, under, type FieldError } from './errors';
	import { REQUIREMENT_SOURCES, newCustomRequirement } from './factories';
	import { moved, removedAt } from './lists';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';

	interface Props {
		id: string;
		block: RequirementsBlock;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, block = $bindable(), errors = [], readOnly = false }: Props = $props();

	function toggleSource(source: RequirementSource, on: boolean) {
		const set = new Set(block.sources);
		if (on) set.add(source);
		else set.delete(source);
		// Keep the catalogue order.
		block.sources = REQUIREMENT_SOURCES.map((s) => s.value).filter((s) => set.has(s));
	}

	function add(text: string) {
		block.custom = [...block.custom, newCustomRequirement(text, block.custom)];
	}

	const NOTE_ITEMS = [
		{ value: 'off', label: 'No notes' },
		{ value: 'optional', label: 'Optional' },
		{ value: 'required_unless_met', label: 'Required unless met' },
	];
</script>

<div class="space-y-4">
	<fieldset class="space-y-2">
		<legend class="section-label mb-1">From the app spec</legend>
		<p class="text-[11px] text-muted-foreground">
			Items are built from each app's spec as frozen when the task opens; apps of the same spec get identical items. Endpoints read “METHOD /path — description”.
		</p>
		<div class="grid gap-1.5 sm:grid-cols-2">
			{#each REQUIREMENT_SOURCES as source (source.value)}
				<label class="flex items-center gap-2 text-sm">
					<Checkbox
						checked={block.sources.includes(source.value)}
						disabled={readOnly}
						onCheckedChange={(v) => toggleSource(source.value, !!v)}
					/>
					{source.label}
				</label>
			{/each}
		</div>
		{#each messagesUnder(under(errors, 'sources')) as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
	</fieldset>

	<div class="space-y-2">
		<p class="section-label">Extra items <span class="normal-case tracking-normal">(asked for every app)</span></p>
		{#if block.custom.length === 0}
			<p class="text-sm text-muted-foreground">None.</p>
		{/if}
		<ol class="space-y-1.5">
			{#each block.custom as item, i (item.id)}
				{@const errs = under(errors, 'custom', [i, item.id])}
				<li class="space-y-1">
					<div class="flex items-center gap-2">
						<span class="w-6 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
						{#if readOnly}
							<span class="min-w-0 flex-1 text-sm">{item.text}</span>
						{:else}
							<Input
								class="h-8 min-w-0 flex-1 text-sm"
								aria-label="Extra requirement {i + 1}"
								bind:value={block.custom[i].text}
								maxlength={1000}
								aria-invalid={errs.length ? true : undefined}
							/>
						{/if}
						<span class="hidden w-32 truncate font-mono text-[11px] text-muted-foreground sm:block" title="Stable id">c_{item.id}</span>
						{#if !readOnly}
							<ListItemActions
								name={item.text}
								index={i}
								count={block.custom.length}
								onmove={(d) => (block.custom = moved(block.custom, i, d))}
								onremove={() => (block.custom = removedAt(block.custom, i))}
							/>
						{/if}
					</div>
					{#each [...messagesAt(errs), ...messagesAt(errs, 'text'), ...messagesAt(errs, 'id')] as message (message)}
						<p class="pl-8 text-[11px] text-danger-fg">{message}</p>
					{/each}
				</li>
			{/each}
		</ol>
		{#each messagesAt(errors, 'custom') as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
		{#if !readOnly}
			<AddEntryForm id="{id}-add-custom" placeholder="e.g. Data survives a page reload" buttonLabel="Add item" onadd={add} maxlength={1000} />
		{/if}
	</div>

	<div class="space-y-1.5">
		<p class="section-label" id="{id}-note-label">Notes per item</p>
		<Segmented
			size="sm"
			aria-labelledby="{id}-note-label"
			value={block.note}
			disabled={readOnly}
			onValueChange={(v) => (block.note = v as RequirementsBlock['note'])}
			items={NOTE_ITEMS}
		/>
		<p class="text-[11px] {messageAt(errors, 'note') ? 'text-danger-fg' : 'text-muted-foreground'}">
			{messageAt(errors, 'note') ||
				'Verdicts: met, partial, not met, blocked (an app defect prevents the test; scores 0) and cannot test (excluded).'}
		</p>
	</div>
</div>
