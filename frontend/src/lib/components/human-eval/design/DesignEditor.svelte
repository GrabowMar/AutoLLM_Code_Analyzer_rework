<script lang="ts">
	/**
	 * DesignEditor — the whole task design: evaluation blocks (add by type for
	 * the mode, reorder, duplicate, remove, edit), consent, the questions asked
	 * before the first and after the last app, and the pre-registered analysis
	 * plan. Server validation errors (`validate-design`, create/update) are shown
	 * next to their fields and listed in a summary.
	 *
	 *   <DesignEditor mode={mode} bind:design environment={env} errors={validation.errors} />
	 *
	 * Props:
	 * - `mode`: 'single' | 'pairwise' — limits the block types offered.
	 * - `design` (bindable): edited in place.
	 * - `environment`: default security checklist and instrument wording (null while loading).
	 * - `readOnly`: render without editing controls (frozen designs).
	 * - `errors`: `{path, message}[]`; design paths with or without a `design.` prefix.
	 * - `allocationStrategy` (optional): shows only the planned-sample field that applies.
	 */
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Plus from '@lucide/svelte/icons/plus';
	import type { AllocationStrategy, Block, BlockType, HumanEvalEnvironment, TaskDesign, TaskMode } from '$lib/api/types/human-eval';
	import { areaErrors, classifyPath, describeDesignPath, messagesAt, under, type FieldError, type PathError } from './errors';
	import { BLOCK_TYPE_INFO, allowedBlockTypes, clonePlain, disallowedBlocks, duplicateBlock, newBlock, normalizeDesign } from './factories';
	import { BLOCK_ICONS } from './icons';
	import { insertedAfter, moved, removedAt } from './lists';
	import { reconcilePlan, withAddedBlocks } from './plan';
	import AnalysisPlanEditor from './AnalysisPlanEditor.svelte';
	import BlockCard from './BlockCard.svelte';
	import ConsentEditor from './ConsentEditor.svelte';
	import QuestionListEditor from './QuestionListEditor.svelte';

	interface Props {
		mode: TaskMode;
		design: TaskDesign;
		environment: HumanEvalEnvironment | null;
		readOnly?: boolean;
		errors?: PathError[];
		allocationStrategy?: AllocationStrategy | null;
	}

	let { mode, design = $bindable(), environment, readOnly = false, errors = [], allocationStrategy = null }: Props = $props();

	const MAX_BLOCKS = 30;

	// Older or partial payloads: fill the parts the editors rely on (no-op for normalized designs).
	if (!design.analysis?.exclusion_rules || !design.consent || !Array.isArray(design.pre_questions) || !Array.isArray(design.post_questions) || !Array.isArray(design.blocks)) {
		design = normalizeDesign(design);
	}

	// Design-scoped errors (+ request-level ones without a path).
	const designErrors = $derived<FieldError[]>([
		...areaErrors(errors, 'design'),
		...(errors ?? [])
			.filter((e) => classifyPath(e.path ?? '').area === 'general' && classifyPath(e.path ?? '').segments.length === 0)
			.map((e) => ({ path: [], message: e.message })),
	]);
	const summary = $derived(
		designErrors.map((e) => ({ where: describeDesignPath(e.path, design), message: e.message }))
	);

	const allowedTypes = $derived(allowedBlockTypes(mode));
	const disallowed = $derived(disallowedBlocks(mode, design.blocks));
	const needsPairwise = $derived(mode === 'pairwise' && !design.blocks.some((b) => b.type === 'pairwise'));
	const reviewBlocks = $derived(
		design.blocks.filter((b) => b.type === 'security' && b.finding_review.enabled).map((b) => ({ id: b.id, title: b.title }))
	);
	const blockIds = $derived(design.blocks.map((b) => b.id));

	function otherReviewBlock(blockId: string): string | null {
		const other = reviewBlocks.find((b) => b.id !== blockId);
		return other ? other.title || other.id : null;
	}

	/** Replace the blocks after an add; a primary that pointed at no block is cleared, never adopted by the new one. */
	function setAddedBlocks(nextBlocks: Block[]) {
		const next = withAddedBlocks(design, nextBlocks);
		if (next.analysis !== design.analysis) design.analysis = next.analysis;
		design.blocks = next.blocks;
	}

	function addBlock(type: BlockType) {
		if (design.blocks.length >= MAX_BLOCKS) return;
		setAddedBlocks([...design.blocks, newBlock(type, blockIds, environment)]);
	}

	function duplicate(index: number) {
		if (design.blocks.length >= MAX_BLOCKS) return;
		const copy = duplicateBlock(design.blocks[index], blockIds);
		if (copy.type === 'security' && copy.finding_review.enabled) {
			// Only one security block may review findings.
			copy.finding_review.enabled = false;
		}
		setAddedBlocks(insertedAfter(design.blocks, index, copy));
	}

	function remove(index: number) {
		const removed = clonePlain(design.blocks[index]);
		const previousPlan = clonePlain(design.analysis);
		design.blocks = removedAt(design.blocks, index);
		design.analysis = reconcilePlan(design.analysis, design.blocks);
		toast(`Removed “${removed.title || BLOCK_TYPE_INFO[removed.type].title}”`, {
			action: {
				label: 'Undo',
				onClick: () => {
					if (design.blocks.some((b) => b.id === removed.id)) return;
					const next = [...design.blocks];
					next.splice(Math.min(index, next.length), 0, removed);
					design.blocks = next;
					design.analysis = previousPlan;
				},
			},
		});
	}

	function removeDisallowed() {
		const drop = new Set(disallowed.map((b) => b.id));
		design.blocks = design.blocks.filter((b) => !drop.has(b.id));
		design.analysis = reconcilePlan(design.analysis, design.blocks);
	}
</script>

<div class="space-y-10">
	{#if summary.length > 0}
		<div class="rounded-md border border-destructive/40 bg-destructive/5 px-4 py-3" role="alert">
			<p class="flex items-center gap-2 text-sm font-medium text-danger-fg">
				<CircleAlert class="size-4 shrink-0" />
				{summary.length} problem{summary.length === 1 ? '' : 's'} in the design
			</p>
			<ul class="mt-1.5 space-y-0.5 text-xs">
				{#each summary.slice(0, 15) as item, i (i)}
					<li><span class="text-muted-foreground">{item.where}:</span> {item.message}</li>
				{/each}
				{#if summary.length > 15}<li class="text-muted-foreground">… and {summary.length - 15} more</li>{/if}
			</ul>
		</div>
	{/if}

	<section class="space-y-3" aria-labelledby="design-blocks-title">
		<SectionHeading
			id="design-blocks-title"
			size="sm"
			title="Evaluation blocks"
			count={design.blocks.length}
			lead="What participants do for every {mode === 'pairwise' ? 'pair of apps' : 'app'}, in this order."
		/>

		{#if !readOnly && disallowed.length > 0}
			<div class="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning-fg">
				<span>
					{disallowed.length} block{disallowed.length === 1 ? ' is' : 's are'} not available in {mode === 'pairwise' ? 'pairwise' : 'single-app'} mode.
				</span>
				<Button variant="outline" size="sm" class="h-7 text-xs" onclick={removeDisallowed}>Remove {disallowed.length}</Button>
			</div>
		{/if}
		{#if needsPairwise}
			<div class="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning-fg">
				<span>Pairwise tasks need a pairwise preference block.</span>
				{#if !readOnly}<Button variant="outline" size="sm" class="h-7 text-xs" onclick={() => addBlock('pairwise')}>Add one</Button>{/if}
			</div>
		{/if}

		{#if design.blocks.length === 0}
			<p class="rounded-md border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
				No blocks yet{readOnly ? '.' : ' — add the first one below.'}
			</p>
		{/if}

		<div class="space-y-3">
			{#each design.blocks as block, i (block.id)}
				<BlockCard
					id="block-{block.id}"
					bind:block={design.blocks[i]}
					index={i}
					count={design.blocks.length}
					{mode}
					{environment}
					errors={under(designErrors, 'blocks', [i, block.id])}
					{readOnly}
					otherReviewBlock={otherReviewBlock(block.id)}
					onmove={(d) => (design.blocks = moved(design.blocks, i, d))}
					onduplicate={() => duplicate(i)}
					onremove={() => remove(i)}
				/>
			{/each}
		</div>
		{#each messagesAt(designErrors, 'blocks') as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}

		{#if !readOnly}
			<div class="space-y-2">
				<p class="section-label">Add a block</p>
				<div class="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
					{#each allowedTypes as type (type)}
						{@const Icon = BLOCK_ICONS[type]}
						<button
							type="button"
							class="group flex cursor-pointer items-start gap-3 rounded-md border border-border bg-card px-3 py-2.5 text-left transition-colors duration-150 hover:border-primary/50 hover:bg-primary/5 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none"
							disabled={design.blocks.length >= MAX_BLOCKS}
							onclick={() => addBlock(type)}
						>
							<span class="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded border border-border bg-surface-2 text-muted-foreground group-hover:text-primary" aria-hidden="true">
								<Icon class="size-3.5" />
							</span>
							<span class="min-w-0">
								<span class="flex items-center gap-1 text-sm font-medium"><Plus class="size-3.5 text-muted-foreground" />{BLOCK_TYPE_INFO[type].label}</span>
								<span class="block text-xs text-muted-foreground">{BLOCK_TYPE_INFO[type].description}</span>
							</span>
						</button>
					{/each}
				</div>
				{#if design.blocks.length >= MAX_BLOCKS}
					<p class="text-[11px] text-muted-foreground">A design holds at most {MAX_BLOCKS} blocks.</p>
				{/if}
			</div>
		{/if}
	</section>

	<section class="space-y-3" aria-labelledby="design-consent-title">
		<SectionHeading id="design-consent-title" size="sm" title="Consent" />
		<Card.Root>
			<Card.Content>
				<ConsentEditor id="design-consent" bind:consent={design.consent} errors={under(designErrors, 'consent')} {readOnly} />
			</Card.Content>
		</Card.Root>
	</section>

	<section class="space-y-3" aria-labelledby="design-pre-title">
		<SectionHeading
			id="design-pre-title"
			size="sm"
			title="Before the first app"
			count={design.pre_questions.length}
			lead="Asked once per participant, e.g. programming, web and security experience. Attention checks go here or at the end."
		/>
		<Card.Root>
			<Card.Content>
				<QuestionListEditor
					id="design-pre"
					bind:questions={design.pre_questions}
					errors={under(designErrors, 'pre_questions')}
					{readOnly}
					emptyText="No questions before the first app."
				/>
			</Card.Content>
		</Card.Root>
		{#each messagesAt(designErrors, 'pre_questions') as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
	</section>

	<section class="space-y-3" aria-labelledby="design-post-title">
		<SectionHeading
			id="design-post-title"
			size="sm"
			title="At the end"
			count={design.post_questions.length}
			lead="Asked once when the participant finishes, before the completion code."
		/>
		<Card.Root>
			<Card.Content>
				<QuestionListEditor
					id="design-post"
					bind:questions={design.post_questions}
					errors={under(designErrors, 'post_questions')}
					{readOnly}
					emptyText="No closing questions."
				/>
			</Card.Content>
		</Card.Root>
	</section>

	<section class="space-y-3" aria-labelledby="design-plan-title">
		<SectionHeading
			id="design-plan-title"
			size="sm"
			title="Analysis plan"
			lead="Pre-registered: frozen with the design when the task first opens."
		/>
		<Card.Root>
			<Card.Content>
				<AnalysisPlanEditor
					id="design-plan"
					bind:plan={design.analysis}
					blocks={design.blocks}
					{allocationStrategy}
					errors={under(designErrors, 'analysis')}
					{readOnly}
				/>
			</Card.Content>
		</Card.Root>
	</section>
</div>
