<script lang="ts">
	/**
	 * PairwiseBlockEditor — preference criteria, the answer scale (A / tie / B
	 * or five points), the overall question and the rationale rule.
	 *
	 * Props: `id`, `block` (bindable), `errors` (relative to the block), `readOnly`.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { CommentMode, PairwiseBlock, PairwiseScale } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, under, type FieldError } from './errors';
	import { newPairwiseCriterion } from './factories';
	import { moved, removedAt } from './lists';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';

	interface Props {
		id: string;
		block: PairwiseBlock;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, block = $bindable(), errors = [], readOnly = false }: Props = $props();

	function add(label: string) {
		block.criteria = [...block.criteria, newPairwiseCriterion(label, block.criteria)];
	}
</script>

<div class="space-y-4">
	<div class="space-y-2">
		<p class="section-label">Criteria</p>
		{#if block.criteria.length === 0}
			<p class="text-sm text-muted-foreground">No criteria{block.overall ? ' — only the overall preference is asked' : ' — add one or ask an overall preference'}.</p>
		{/if}
		<ol class="space-y-1.5">
			{#each block.criteria as criterion, i (criterion.id)}
				{@const errs = under(errors, 'criteria', [i, criterion.id])}
				<li class="space-y-1">
					<div class="flex flex-wrap items-center gap-2">
						<span class="w-6 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
						{#if readOnly}
							<span class="min-w-0 flex-1 text-sm font-medium">{criterion.label}</span>
							{#if criterion.help}<span class="w-full pl-8 text-xs text-muted-foreground">{criterion.help}</span>{/if}
						{:else}
							<Input
								class="h-8 min-w-40 flex-1 text-sm font-medium"
								aria-label="Criterion {i + 1} label"
								bind:value={block.criteria[i].label}
								maxlength={300}
								aria-invalid={messageAt(errs, 'label') ? true : undefined}
							/>
							<Input
								class="h-8 min-w-40 flex-[2] text-sm"
								aria-label="Criterion {i + 1} help"
								placeholder="Help (optional)"
								bind:value={() => block.criteria[i].help ?? '', (v) => (block.criteria[i].help = v)}
								maxlength={5000}
							/>
							<span class="hidden w-24 truncate font-mono text-[11px] text-muted-foreground lg:block" title="Stable id">{criterion.id}</span>
							<ListItemActions
								name={criterion.label}
								index={i}
								count={block.criteria.length}
								onmove={(d) => (block.criteria = moved(block.criteria, i, d))}
								onremove={() => (block.criteria = removedAt(block.criteria, i))}
							/>
						{/if}
					</div>
					{#each [...messagesAt(errs), ...messagesAt(errs, 'label'), ...messagesAt(errs, 'help'), ...messagesAt(errs, 'id')] as message (message)}
						<p class="pl-8 text-[11px] text-danger-fg">{message}</p>
					{/each}
				</li>
			{/each}
		</ol>
		{#each messagesAt(errors, 'criteria') as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
		{#if !readOnly}
			<AddEntryForm id="{id}-add-criterion" placeholder="New criterion, e.g. Visual design" buttonLabel="Add criterion" onadd={add} />
		{/if}
	</div>

	<div class="grid gap-4 sm:grid-cols-2">
		<div class="space-y-1.5">
			<p class="section-label" id="{id}-scale-label">Answer scale</p>
			<Segmented
				size="sm"
				aria-labelledby="{id}-scale-label"
				value={block.scale}
				disabled={readOnly}
				onValueChange={(v) => (block.scale = v as PairwiseScale)}
				items={[
					{ value: 'ab_tie', label: 'A / tie / B' },
					{ value: 'five_point', label: '5-point', title: 'A much better … B much better' },
				]}
			/>
			<p class="text-[11px] {messageAt(errors, 'scale') ? 'text-danger-fg' : 'text-muted-foreground'}">
				{messageAt(errors, 'scale') ||
					(block.scale === 'five_point' ? 'A much better, A better, no difference, B better, B much better.' : 'A better, no difference, B better.')}
				Sides are randomised per assignment.
			</p>
		</div>
		<div class="space-y-1.5">
			<p class="section-label" id="{id}-rationale-label">Rationale</p>
			<Segmented
				size="sm"
				aria-labelledby="{id}-rationale-label"
				value={block.rationale}
				disabled={readOnly}
				onValueChange={(v) => (block.rationale = v as CommentMode)}
				items={[
					{ value: 'off', label: 'Off' },
					{ value: 'optional', label: 'Optional' },
					{ value: 'required', label: 'Required' },
				]}
			/>
			{#if messageAt(errors, 'rationale')}<p class="text-[11px] text-danger-fg">{messageAt(errors, 'rationale')}</p>{/if}
		</div>
	</div>
	<label class="flex items-start gap-2.5 text-sm">
		<Checkbox class="mt-0.5" bind:checked={block.overall} disabled={readOnly} />
		<span>
			<span class="font-medium">Ask an overall preference</span>
			<span class="block text-xs text-muted-foreground">One more question after the criteria: which app is better overall.</span>
		</span>
	</label>
	{#each messagesAt(errors) as message (message)}
		<p class="text-[11px] text-danger-fg">{message}</p>
	{/each}
</div>
