<script lang="ts">
	/**
	 * RatingBlockEditor — rubric criteria (label, help, scale, required, "can't
	 * judge") and the comment rule of a rating block.
	 *
	 * Props: `id`, `block` (bindable), `errors` (relative to the block), `readOnly`.
	 */
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { CommentMode, RatingBlock } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, under, type FieldError } from './errors';
	import { clonePlain, newCriterion } from './factories';
	import { uniqueId } from './ids';
	import { insertedAfter, moved, removedAt } from './lists';
	import { cloneScale, scaleIssues, scaleSummary } from './scale';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';
	import ScaleEditor from './ScaleEditor.svelte';

	interface Props {
		id: string;
		block: RatingBlock;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, block = $bindable(), errors = [], readOnly = false }: Props = $props();

	const COMMENT_ITEMS = [
		{ value: 'off', label: 'No comment' },
		{ value: 'optional', label: 'Optional' },
		{ value: 'required', label: 'Required' },
	];

	function add(label: string) {
		const last = block.criteria[block.criteria.length - 1];
		block.criteria = [...block.criteria, newCriterion(label, block.criteria, last?.scale)];
	}

	function duplicate(index: number) {
		const copy = clonePlain(block.criteria[index]);
		copy.id = uniqueId(copy.id, block.criteria.map((c) => c.id));
		copy.label = `${copy.label} (copy)`.slice(0, 300);
		block.criteria = insertedAfter(block.criteria, index, copy);
	}

	// Scale editors fold to a one-line summary; a scale with problems stays open.
	let openScales = $state(new Set<string>());
	function scaleOpen(criterionId: string, hasProblems: boolean): boolean {
		return hasProblems || openScales.has(criterionId);
	}
	function toggleScale(criterionId: string) {
		const next = new Set(openScales);
		if (next.has(criterionId)) next.delete(criterionId);
		else next.add(criterionId);
		openScales = next;
	}

	function scaleToAll(index: number) {
		const scale = block.criteria[index].scale;
		for (const c of block.criteria) c.scale = cloneScale(scale);
	}
</script>

<div class="space-y-4">
	<div class="space-y-2">
		<p class="section-label">Criteria</p>
		{#if block.criteria.length === 0}
			<p class="text-sm text-muted-foreground">No criteria yet — add at least one.</p>
		{/if}
		<ol class="space-y-2">
			{#each block.criteria as criterion, i (criterion.id)}
				{@const errs = under(errors, 'criteria', [i, criterion.id])}
				<li class="rounded-md border bg-surface-1 {errs.length ? 'border-destructive/40' : 'border-border'}">
					<div class="flex items-center gap-2 border-b border-border px-3 py-1.5">
						<span class="font-mono text-[11px] tabular-nums text-muted-foreground">C{i + 1}</span>
						{#if readOnly}
							<span class="min-w-0 flex-1 truncate text-sm font-medium">{criterion.label}</span>
						{:else}
							<Input
								class="h-8 min-w-0 flex-1 text-sm font-medium"
								aria-label="Criterion {i + 1} label"
								bind:value={block.criteria[i].label}
								maxlength={300}
								aria-invalid={messageAt(errs, 'label') ? true : undefined}
							/>
						{/if}
						<span class="hidden font-mono text-[11px] text-muted-foreground sm:inline" title="Stable id (exports, analysis)">{criterion.id}</span>
						{#if !readOnly}
							<ListItemActions
								name={criterion.label}
								index={i}
								count={block.criteria.length}
								onmove={(d) => (block.criteria = moved(block.criteria, i, d))}
								onduplicate={() => duplicate(i)}
								onremove={() => (block.criteria = removedAt(block.criteria, i))}
							/>
						{/if}
					</div>
					<div class="space-y-3 px-3 py-2.5">
						{#if messageAt(errs, 'label')}<p class="text-[11px] text-danger-fg">{messageAt(errs, 'label')}</p>{/if}
						{#if readOnly}
							{#if criterion.help}<p class="text-xs text-muted-foreground">{criterion.help}</p>{/if}
							<p class="text-xs text-muted-foreground">
								Scale {scaleSummary(criterion.scale)}{criterion.required ? ' · required' : ' · optional'}{criterion.allow_na ? ' · “can’t judge” allowed' : ''}
							</p>
						{:else}
							<div class="space-y-1.5">
								<Label class="text-xs" for="{id}-c-{criterion.id}-help">Help text <span class="text-muted-foreground">(what to look at)</span></Label>
								<Input
									id="{id}-c-{criterion.id}-help"
									class="h-8 text-sm"
									bind:value={() => block.criteria[i].help ?? '', (v) => (block.criteria[i].help = v)}
									maxlength={5000}
								/>
							</div>
							<div class="flex flex-wrap items-center gap-x-5 gap-y-2">
								<label class="flex items-center gap-2 text-sm">
									<Checkbox bind:checked={block.criteria[i].required} />
									Required
								</label>
								<label class="flex items-center gap-2 text-sm" title="Participants may answer “can’t judge” (stored as na, left out of means)">
									<Checkbox bind:checked={block.criteria[i].allow_na} />
									Offer “can’t judge”
								</label>
								{#if block.criteria.length > 1}
									<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={() => scaleToAll(i)}>Use this scale for all criteria</Button>
								{/if}
							</div>
							{@const scaleErrs = under(errs, 'scale')}
							{@const problems = scaleErrs.length > 0 || scaleIssues(criterion.scale).length > 0}
							{@const open = scaleOpen(criterion.id, problems)}
							<div class="flex flex-wrap items-center gap-2 text-xs">
								<span class="section-label">Scale</span>
								<span class="font-mono tabular-nums">{scaleSummary(criterion.scale)}</span>
								{#if criterion.scale.min_label || criterion.scale.max_label}
									<span class="text-muted-foreground">“{criterion.scale.min_label || criterion.scale.min}” → “{criterion.scale.max_label || criterion.scale.max}”</span>
								{/if}
								{#if criterion.scale.labels && Object.keys(criterion.scale.labels).length}
									<span class="text-muted-foreground">· {Object.keys(criterion.scale.labels).length} value labels</span>
								{/if}
								<Button
									variant="ghost"
									size="sm"
									class="h-7 text-xs"
									disabled={problems}
									aria-expanded={open}
									aria-controls="{id}-c-{criterion.id}-scale-editor"
									onclick={() => toggleScale(criterion.id)}
								>
									{open ? 'Done' : 'Edit scale'}
								</Button>
							</div>
							{#if open}
								<div id="{id}-c-{criterion.id}-scale-editor" class="rounded-md border border-border bg-background/40 p-3">
									<ScaleEditor id="{id}-c-{criterion.id}-scale" bind:scale={block.criteria[i].scale} errors={scaleErrs} />
								</div>
							{/if}
						{/if}
						{#each [...messagesAt(errs), ...messagesAt(errs, 'help'), ...messagesAt(errs, 'id')] as message (message)}
							<p class="text-[11px] text-danger-fg">{message}</p>
						{/each}
					</div>
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

	<div class="space-y-1.5">
		<p class="section-label" id="{id}-comment-label">Comment</p>
		<Segmented
			size="sm"
			aria-labelledby="{id}-comment-label"
			value={block.comment}
			disabled={readOnly}
			onValueChange={(v) => (block.comment = v as CommentMode)}
			items={COMMENT_ITEMS}
		/>
		{#if messageAt(errors, 'comment')}<p class="text-[11px] text-danger-fg">{messageAt(errors, 'comment')}</p>{/if}
	</div>
</div>
