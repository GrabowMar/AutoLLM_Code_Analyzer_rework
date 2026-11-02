<script lang="ts">
	/**
	 * SecurityBlockEditor — guided checklist (seeded from the server default),
	 * vulnerability reports, TP/FP review of automated findings and the HTTP
	 * console. Reviewed findings show each tool's own label only, never a
	 * normalized severity.
	 *
	 * Props: `id`, `block` (bindable), `environment` (default checklist),
	 * `errors` (relative to the block), `readOnly`, `otherReviewBlock` (title of
	 * another security block that already reviews findings: at most one may).
	 */
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import NumberField from '$lib/components/generator/composer/NumberField.svelte';
	import type { HumanEvalEnvironment, SecurityBlock } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, messagesUnder, under, type FieldError } from './errors';
	import { FINDING_CATEGORIES, clonePlain, newChecklistItem } from './factories';
	import { moved, removedAt } from './lists';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';

	interface Props {
		id: string;
		block: SecurityBlock;
		environment: HumanEvalEnvironment | null;
		errors?: FieldError[];
		readOnly?: boolean;
		otherReviewBlock?: string | null;
	}

	let { id, block = $bindable(), environment, errors = [], readOnly = false, otherReviewBlock = null }: Props = $props();

	const defaults = $derived(environment?.security_checklist_default ?? []);
	const missingDefaults = $derived(defaults.filter((d) => !block.checklist.some((c) => c.id === d.id)));
	const reviewErrors = $derived(under(errors, 'finding_review'));
	const categoryOptions = $derived([
		...FINDING_CATEGORIES,
		...block.finding_review.categories
			.filter((c) => !FINDING_CATEGORIES.some((f) => f.value === c))
			.map((c) => ({ value: c, label: c })),
	]);

	function add(label: string) {
		block.checklist = [...block.checklist, newChecklistItem(label, block.checklist)];
	}

	function addMissingDefaults() {
		block.checklist = [...block.checklist, ...clonePlain(missingDefaults)];
	}

	function resetToDefaults() {
		block.checklist = clonePlain(defaults);
	}

	function toggleCategory(value: string, on: boolean) {
		const set = new Set(block.finding_review.categories);
		if (on) set.add(value);
		else set.delete(value);
		block.finding_review.categories = categoryOptions.map((c) => c.value).filter((v) => set.has(v));
	}
</script>

<div class="space-y-5">
	<div class="space-y-2">
		<div class="flex flex-wrap items-center justify-between gap-2">
			<p class="section-label">Checklist <span class="normal-case tracking-normal">({block.checklist.length} items)</span></p>
			{#if !readOnly && defaults.length > 0}
				<div class="flex flex-wrap gap-1.5">
					{#if missingDefaults.length > 0 && block.checklist.length > 0}
						<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={addMissingDefaults}>Add {missingDefaults.length} default item{missingDefaults.length === 1 ? '' : 's'}</Button>
					{/if}
					<Button variant="ghost" size="sm" class="h-7 text-xs" onclick={resetToDefaults}>
						{block.checklist.length === 0 ? 'Use the default checklist' : 'Reset to default'}
					</Button>
				</div>
			{/if}
		</div>
		<p class="text-[11px] text-muted-foreground">
			For every item the participant records vulnerable / not vulnerable / not tested. The platform's own admin login is out of scope.
		</p>
		{#if block.checklist.length === 0}
			<p class="text-sm text-muted-foreground">No checklist items.</p>
		{/if}
		<ol class="space-y-2">
			{#each block.checklist as item, i (item.id)}
				{@const errs = under(errors, 'checklist', [i, item.id])}
				<li class="rounded-md border bg-surface-1 px-3 py-2 {errs.length ? 'border-destructive/40' : 'border-border'}">
					{#if readOnly}
						<p class="text-sm font-medium">
							{item.label}
							{#if item.category}<span class="ml-1 font-mono text-[11px] font-normal text-muted-foreground">{item.category}</span>{/if}
						</p>
						{#if item.help}<p class="mt-0.5 whitespace-pre-line text-xs text-muted-foreground">{item.help}</p>{/if}
					{:else}
						<div class="flex flex-wrap items-center gap-2">
							<span class="font-mono text-[11px] tabular-nums text-muted-foreground">{i + 1}</span>
							<Input
								class="h-8 min-w-48 flex-1 text-sm font-medium"
								aria-label="Checklist item {i + 1} label"
								bind:value={block.checklist[i].label}
								maxlength={300}
								aria-invalid={messageAt(errs, 'label') ? true : undefined}
							/>
							<Input
								class="h-8 w-full text-xs sm:w-52"
								aria-label="Checklist item {i + 1} category"
								placeholder="Category, e.g. A01:2021"
								bind:value={() => block.checklist[i].category ?? '', (v) => (block.checklist[i].category = v)}
								maxlength={300}
							/>
							<ListItemActions
								name={item.label}
								index={i}
								count={block.checklist.length}
								onmove={(d) => (block.checklist = moved(block.checklist, i, d))}
								onremove={() => (block.checklist = removedAt(block.checklist, i))}
							/>
						</div>
						<Textarea
							class="mt-2 min-h-[52px] text-xs"
							rows={2}
							aria-label="How to test checklist item {i + 1}"
							placeholder="How to test it (shown to participants)"
							bind:value={() => block.checklist[i].help ?? '', (v) => (block.checklist[i].help = v)}
							maxlength={5000}
						/>
					{/if}
					{#each [...messagesAt(errs), ...messagesAt(errs, 'label'), ...messagesAt(errs, 'help'), ...messagesAt(errs, 'category'), ...messagesAt(errs, 'id')] as message (message)}
						<p class="mt-1 text-[11px] text-danger-fg">{message}</p>
					{/each}
				</li>
			{/each}
		</ol>
		{#each messagesAt(errors, 'checklist') as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
		{#if !readOnly}
			<AddEntryForm id="{id}-add-check" placeholder="New checklist item, e.g. File upload accepts scripts" buttonLabel="Add item" onadd={add} />
		{/if}
	</div>

	<fieldset class="space-y-2">
		<legend class="section-label mb-1">Participant tools</legend>
		<label class="flex items-start gap-2.5 text-sm">
			<Checkbox class="mt-0.5" bind:checked={block.reports} disabled={readOnly} />
			<span>
				<span class="font-medium">Vulnerability reports</span>
				<span class="block text-xs text-muted-foreground">Participants file findings with steps, location, their own severity and screenshots.</span>
			</span>
		</label>
		<label class="flex items-start gap-2.5 text-sm">
			<Checkbox class="mt-0.5" bind:checked={block.http_console} disabled={readOnly} />
			<span>
				<span class="font-medium">HTTP console</span>
				<span class="block text-xs text-muted-foreground">Send raw requests to the app from the workbench; every request and response is logged as research data.</span>
			</span>
		</label>
		{#if messageAt(errors, 'reports') || messageAt(errors, 'http_console')}
			<p class="text-[11px] text-danger-fg">{messageAt(errors, 'reports') || messageAt(errors, 'http_console')}</p>
		{/if}
	</fieldset>

	<fieldset class="space-y-3">
		<legend class="section-label mb-1">Review of automated findings</legend>
		<label class="flex items-start gap-2.5 text-sm">
			<Checkbox class="mt-0.5" bind:checked={block.finding_review.enabled} disabled={readOnly || (!!otherReviewBlock && !block.finding_review.enabled)} />
			<span>
				<span class="font-medium">Ask participants to verify tool findings (true / false positive)</span>
				<span class="block text-xs text-muted-foreground">
					A fixed sample per app is drawn when the task opens, so every rater of an app reviews the same findings. Participants see each tool's own label (“unrated” when it gives none).
				</span>
				{#if otherReviewBlock && !block.finding_review.enabled}
					<span class="block text-xs text-warning-fg">“{otherReviewBlock}” already reviews findings; only one security block may.</span>
				{/if}
			</span>
		</label>
		{#if block.finding_review.enabled}
			<div class="space-y-3 pl-6">
				<div class="sm:w-56">
					<NumberField
						id="{id}-max-findings"
						label="Findings per app"
						bind:value={() => block.finding_review.max_findings, (v) => { if (v != null) block.finding_review.max_findings = v; }}
						min={1}
						max={50}
						required
						disabled={readOnly}
						hint="Spread across tools in proportion to their findings (at least one per tool)."
						error={messageAt(reviewErrors, 'max_findings')}
					/>
				</div>
				<div class="space-y-1.5">
					<p class="text-xs font-medium">Finding categories <span class="font-normal text-muted-foreground">(none ticked = all)</span></p>
					<div class="flex flex-wrap gap-x-4 gap-y-1.5">
						{#each categoryOptions as category (category.value)}
							<label class="flex items-center gap-2 text-sm">
								<Checkbox
									checked={block.finding_review.categories.includes(category.value)}
									disabled={readOnly}
									onCheckedChange={(v) => toggleCategory(category.value, !!v)}
								/>
								{category.label}
							</label>
						{/each}
					</div>
				</div>
				<div class="flex flex-wrap gap-x-5 gap-y-1.5">
					<label class="flex items-center gap-2 text-sm" title="Findings of AI review tools">
						<Checkbox bind:checked={block.finding_review.include_ai} disabled={readOnly} />Include AI tools
					</label>
					<label class="flex items-center gap-2 text-sm" title="Vulnerable dependency findings (category “dependency”)">
						<Checkbox bind:checked={block.finding_review.include_dependency} disabled={readOnly} />Include dependency findings
					</label>
					<label class="flex items-center gap-2 text-sm" title="Findings of tools that scan the running app">
						<Checkbox bind:checked={block.finding_review.include_dynamic} disabled={readOnly} />Include dynamic scanners
					</label>
				</div>
			</div>
		{/if}
		{#each messagesUnder(reviewErrors.filter((e) => e.path[0] !== 'max_findings')) as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
	</fieldset>
</div>
