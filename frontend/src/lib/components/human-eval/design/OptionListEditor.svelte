<script lang="ts">
	/**
	 * OptionListEditor — answer options of a single / multi / attention
	 * question. Values are derived from the label when an option is added and
	 * stay fixed afterwards (stored answers reference them).
	 *
	 * Props: `id`, `options` (bindable), `errors` (relative to the options list),
	 * `readOnly`, `onremoved(value)` (lets the question drop an `expected` that
	 * pointed at a removed option).
	 */
	import { Input } from '$lib/components/ui/input';
	import type { QuestionOption } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, under, type FieldError } from './errors';
	import { newOption } from './factories';
	import { moved, removedAt } from './lists';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';

	interface Props {
		id: string;
		options: QuestionOption[];
		errors?: FieldError[];
		readOnly?: boolean;
		onremoved?: (value: string) => void;
	}

	let { id, options = $bindable([]), errors = [], readOnly = false, onremoved }: Props = $props();

	const listMessages = $derived(messagesAt(errors));

	function add(label: string) {
		options = [...options, newOption(label, options)];
	}

	function remove(index: number) {
		const value = options[index]?.value;
		options = removedAt(options, index);
		if (value) onremoved?.(value);
	}
</script>

<div class="space-y-1.5">
	{#if readOnly}
		<ul class="space-y-0.5 text-sm">
			{#each options as option (option.value)}
				<li class="flex items-baseline gap-2">
					<span>{option.label}</span>
					<span class="font-mono text-[11px] text-muted-foreground">{option.value}</span>
				</li>
			{/each}
		</ul>
	{:else}
		{#each options as option, i (option.value)}
			{@const errs = under(errors, [i, option.value])}
			<div class="flex items-center gap-2">
				<span class="w-5 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
				<div class="min-w-0 flex-1">
					<Input
						class="h-8 text-sm"
						aria-label="Option {i + 1} label"
						bind:value={options[i].label}
						maxlength={300}
						aria-invalid={messageAt(errs, 'label') ? true : undefined}
					/>
				</div>
				<span class="hidden w-28 truncate font-mono text-[11px] text-muted-foreground sm:block" title="Stored value">{option.value}</span>
				<ListItemActions
					name={option.label}
					index={i}
					count={options.length}
					onmove={(d) => (options = moved(options, i, d))}
					onremove={() => remove(i)}
				/>
			</div>
			{#each [...messagesAt(errs), ...messagesAt(errs, 'label'), ...messagesAt(errs, 'value')] as message (message)}
				<p class="pl-7 text-[11px] text-danger-fg">{message}</p>
			{/each}
		{/each}
		<div class="pl-7">
			<AddEntryForm id="{id}-add" placeholder="New option label" buttonLabel="Add option" onadd={add} />
		</div>
	{/if}
	{#each listMessages as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
