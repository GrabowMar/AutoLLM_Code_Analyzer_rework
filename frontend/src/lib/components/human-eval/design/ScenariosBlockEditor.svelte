<script lang="ts">
	/**
	 * ScenariosBlockEditor — usability tasks (title, Markdown description,
	 * success criteria), optional timers and the Single Ease Question.
	 *
	 * Props: `id`, `block` (bindable), `errors` (relative to the block), `readOnly`.
	 */
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import type { ScenariosBlock } from '$lib/api/types/human-eval';
	import { messageAt, messagesAt, under, type FieldError } from './errors';
	import { clonePlain, newScenario } from './factories';
	import { uniqueId } from './ids';
	import { insertedAfter, moved, removedAt } from './lists';
	import AddEntryForm from './AddEntryForm.svelte';
	import ListItemActions from './ListItemActions.svelte';
	import MarkdownField from './MarkdownField.svelte';

	interface Props {
		id: string;
		block: ScenariosBlock;
		errors?: FieldError[];
		readOnly?: boolean;
	}

	let { id, block = $bindable(), errors = [], readOnly = false }: Props = $props();

	function add(title: string) {
		block.scenarios = [...block.scenarios, newScenario(title, block.scenarios)];
	}

	function duplicate(index: number) {
		const copy = clonePlain(block.scenarios[index]);
		copy.id = uniqueId(copy.id, block.scenarios.map((s) => s.id));
		copy.title = `${copy.title} (copy)`.slice(0, 300);
		block.scenarios = insertedAfter(block.scenarios, index, copy);
	}
</script>

<div class="space-y-4">
	<div class="space-y-2">
		<p class="section-label">Scenarios</p>
		{#if block.scenarios.length === 0}
			<p class="text-sm text-muted-foreground">No scenarios yet — add the tasks participants should try to complete.</p>
		{/if}
		<ol class="space-y-2">
			{#each block.scenarios as scenario, i (scenario.id)}
				{@const errs = under(errors, 'scenarios', [i, scenario.id])}
				<li class="rounded-md border bg-surface-1 {errs.length ? 'border-destructive/40' : 'border-border'}">
					<div class="flex items-center gap-2 border-b border-border px-3 py-1.5">
						<span class="font-mono text-[11px] tabular-nums text-muted-foreground">S{i + 1}</span>
						{#if readOnly}
							<span class="min-w-0 flex-1 truncate text-sm font-medium">{scenario.title}</span>
						{:else}
							<Input
								class="h-8 min-w-0 flex-1 text-sm font-medium"
								aria-label="Scenario {i + 1} title"
								bind:value={block.scenarios[i].title}
								maxlength={300}
								aria-invalid={messageAt(errs, 'title') ? true : undefined}
							/>
						{/if}
						<span class="hidden font-mono text-[11px] text-muted-foreground sm:inline" title="Stable id">{scenario.id}</span>
						{#if !readOnly}
							<ListItemActions
								name={scenario.title}
								index={i}
								count={block.scenarios.length}
								onmove={(d) => (block.scenarios = moved(block.scenarios, i, d))}
								onduplicate={() => duplicate(i)}
								onremove={() => (block.scenarios = removedAt(block.scenarios, i))}
							/>
						{/if}
					</div>
					<div class="space-y-3 px-3 py-2.5">
						{#if messageAt(errs, 'title')}<p class="text-[11px] text-danger-fg">{messageAt(errs, 'title')}</p>{/if}
						<MarkdownField
							id="{id}-s-{scenario.id}-description"
							label="What to do (Markdown)"
							bind:value={block.scenarios[i].description}
							rows={4}
							{readOnly}
							placeholder="Sign up, create a note titled “Groceries” and mark it as done."
							error={messageAt(errs, 'description')}
						/>
						{#if readOnly}
							{#if scenario.success_criteria}
								<p class="text-xs"><span class="text-muted-foreground">Success when:</span> {scenario.success_criteria}</p>
							{/if}
						{:else}
							<div class="space-y-1.5">
								<Label class="text-xs" for="{id}-s-{scenario.id}-success">Success criteria <span class="text-muted-foreground">(optional, shown to participants)</span></Label>
								<Textarea
									id="{id}-s-{scenario.id}-success"
									rows={2}
									class="min-h-[52px] text-sm"
									bind:value={() => block.scenarios[i].success_criteria ?? '', (v) => (block.scenarios[i].success_criteria = v)}
									maxlength={20000}
								/>
							</div>
						{/if}
						{#each [...messagesAt(errs), ...messagesAt(errs, 'success_criteria'), ...messagesAt(errs, 'id')] as message (message)}
							<p class="text-[11px] text-danger-fg">{message}</p>
						{/each}
					</div>
				</li>
			{/each}
		</ol>
		{#each messagesAt(errors, 'scenarios') as message (message)}
			<p class="text-[11px] text-danger-fg">{message}</p>
		{/each}
		{#if !readOnly}
			<AddEntryForm id="{id}-add-scenario" placeholder="New scenario, e.g. Create an account" buttonLabel="Add scenario" onadd={add} />
		{/if}
	</div>

	<fieldset class="space-y-2">
		<legend class="section-label mb-1">Per scenario</legend>
		<label class="flex items-start gap-2.5 text-sm">
			<Checkbox class="mt-0.5" bind:checked={block.timed} disabled={readOnly} />
			<span>
				<span class="font-medium">Timer</span>
				<span class="block text-xs text-muted-foreground">Start / stop buttons record the seconds each scenario took.</span>
			</span>
		</label>
		<label class="flex items-start gap-2.5 text-sm">
			<Checkbox class="mt-0.5" bind:checked={block.ease_question} disabled={readOnly} />
			<span>
				<span class="font-medium">Ease rating (Single Ease Question, 1–7)</span>
				<span class="block text-xs text-muted-foreground">Asked after every attempted scenario.</span>
			</span>
		</label>
	</fieldset>
</div>
