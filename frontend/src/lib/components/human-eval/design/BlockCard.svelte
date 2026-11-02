<script lang="ts">
	/**
	 * BlockCard — one design block: header (type, title, id, reorder /
	 * duplicate / remove, fold) and the block's instructions plus its type's
	 * editor. Errors arrive relative to the block (`["title"]`, `["criteria", …]`).
	 *
	 * Props: `id`, `block` (bindable), `index`, `count`, `mode`, `environment`,
	 * `errors`, `readOnly`, `otherReviewBlock`, `onmove(delta)`, `onduplicate`,
	 * `onremove`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import type {
		Block,
		BugsBlock,
		HumanEvalEnvironment,
		PairwiseBlock,
		QuestionnaireBlock,
		RatingBlock,
		RequirementsBlock,
		ScenariosBlock,
		SecurityBlock,
		TaskMode,
	} from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, type FieldError } from './errors';
	import { BLOCK_TYPE_INFO, blockAllowed } from './factories';
	import { BLOCK_ICONS } from './icons';
	import ListItemActions from './ListItemActions.svelte';
	import MarkdownField from './MarkdownField.svelte';
	import RatingBlockEditor from './RatingBlockEditor.svelte';
	import RequirementsBlockEditor from './RequirementsBlockEditor.svelte';
	import SecurityBlockEditor from './SecurityBlockEditor.svelte';
	import ScenariosBlockEditor from './ScenariosBlockEditor.svelte';
	import QuestionnaireBlockEditor from './QuestionnaireBlockEditor.svelte';
	import BugsBlockEditor from './BugsBlockEditor.svelte';
	import PairwiseBlockEditor from './PairwiseBlockEditor.svelte';

	interface Props {
		id: string;
		block: Block;
		index: number;
		count: number;
		mode: TaskMode;
		environment: HumanEvalEnvironment | null;
		errors?: FieldError[];
		readOnly?: boolean;
		otherReviewBlock?: string | null;
		onmove: (delta: number) => void;
		onduplicate: () => void;
		onremove: () => void;
	}

	let {
		id,
		block = $bindable(),
		index,
		count,
		mode,
		environment,
		errors = [],
		readOnly = false,
		otherReviewBlock = null,
		onmove,
		onduplicate,
		onremove,
	}: Props = $props();

	let open = $state(true);
	const info = $derived(BLOCK_TYPE_INFO[block.type]);
	const Icon = $derived(BLOCK_ICONS[block.type]);
	const allowed = $derived(blockAllowed(mode, block.type));
	const hasErrors = $derived(errors.length > 0);
	const bodyId = $derived(`${id}-body`);
	// Errors on fields the header shows (or none of the editors place).
	const headerMessages = $derived([...messagesAt(errors), ...messagesAt(errors, 'type'), ...messagesAt(errors, 'id')]);

	$effect(() => {
		// A folded block with errors unfolds itself.
		if (hasErrors) open = true;
	});
</script>

<Card.Root {id} class="scroll-mt-20 gap-0 py-0 {hasErrors || !allowed ? 'border-destructive/40' : ''}">
	<div class="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-border px-3 py-2">
		<Button
			variant="ghost"
			size="icon"
			class="size-7 text-muted-foreground"
			aria-expanded={open}
			aria-controls={bodyId}
			aria-label="{open ? 'Fold' : 'Unfold'} block {index + 1}"
			onclick={() => (open = !open)}
		>
			<ChevronDown class="size-4 transition-transform duration-150 motion-reduce:transition-none {open ? '' : '-rotate-90'}" />
		</Button>
		<span class="flex size-7 shrink-0 items-center justify-center rounded border border-border bg-surface-2 text-muted-foreground" aria-hidden="true">
			<Icon class="size-3.5" />
		</span>
		<span class="font-mono text-[11px] tabular-nums text-muted-foreground">{index + 1}</span>
		<div class="min-w-40 flex-1">
			{#if readOnly}
				<h3 class="truncate text-sm font-semibold">{block.title || info.title}</h3>
			{:else}
				<Input
					class="h-8 text-sm font-semibold"
					aria-label="Block {index + 1} title"
					bind:value={block.title}
					maxlength={300}
					aria-invalid={messageAt(errors, 'title') ? true : undefined}
				/>
			{/if}
		</div>
		<span class="rounded border border-border px-1.5 py-0.5 text-[11px] text-muted-foreground">{info.label}</span>
		<span class="hidden font-mono text-[11px] text-muted-foreground md:inline" title="Stable block id (answers, exports, analysis plan)">{block.id}</span>
		{#if !readOnly}
			<ListItemActions name={block.title} {index} {count} {onmove} {onduplicate} {onremove} />
		{/if}
	</div>
	{#if !allowed}
		<p class="flex items-center gap-1.5 border-b border-border bg-destructive/5 px-3 py-1.5 text-xs text-danger-fg">
			<TriangleAlert class="size-3.5 shrink-0" />
			{info.label} blocks are not available in {mode === 'pairwise' ? 'pairwise' : 'single-app'} mode — remove this block or switch the mode.
		</p>
	{/if}
	{#if messageAt(errors, 'title') || headerMessages.length}
		<div class="space-y-0.5 border-b border-border px-3 py-1.5">
			{#if messageAt(errors, 'title')}<p class="text-[11px] text-danger-fg">Title: {messageAt(errors, 'title')}</p>{/if}
			{#each headerMessages as message (message)}
				<p class="text-[11px] text-danger-fg">{message}</p>
			{/each}
		</div>
	{/if}
	<div id={bodyId} class="space-y-5 px-4 py-4" hidden={!open}>
		{#if !readOnly || block.instructions}
			<MarkdownField
				id="{id}-instructions"
				label={readOnly ? 'Instructions' : 'Instructions for this block (optional, Markdown)'}
				bind:value={() => block.instructions ?? '', (v) => (block.instructions = v)}
				rows={3}
				{readOnly}
				placeholder={info.description}
				error={messageAt(errors, 'instructions')}
			/>
		{/if}
		{#if block.type === 'rating'}
			<RatingBlockEditor {id} bind:block={() => block as RatingBlock, (v) => (block = v)} {errors} {readOnly} />
		{:else if block.type === 'requirements'}
			<RequirementsBlockEditor {id} bind:block={() => block as RequirementsBlock, (v) => (block = v)} {errors} {readOnly} />
		{:else if block.type === 'security'}
			<SecurityBlockEditor {id} bind:block={() => block as SecurityBlock, (v) => (block = v)} {environment} {errors} {readOnly} {otherReviewBlock} />
		{:else if block.type === 'scenarios'}
			<ScenariosBlockEditor {id} bind:block={() => block as ScenariosBlock, (v) => (block = v)} {errors} {readOnly} />
		{:else if block.type === 'questionnaire'}
			<QuestionnaireBlockEditor {id} bind:block={() => block as QuestionnaireBlock, (v) => (block = v)} {environment} {errors} {readOnly} />
		{:else if block.type === 'bugs'}
			<BugsBlockEditor {id} bind:block={() => block as BugsBlock, (v) => (block = v)} {mode} {errors} {readOnly} />
		{:else if block.type === 'pairwise'}
			<PairwiseBlockEditor {id} bind:block={() => block as PairwiseBlock, (v) => (block = v)} {errors} {readOnly} />
		{/if}
	</div>
</Card.Root>
