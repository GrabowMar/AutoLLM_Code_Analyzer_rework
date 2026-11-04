<script lang="ts">
	/**
	 * PromptTemplateEditor — a prompt template (`PromptTemplateContent`): role, modes, declared variables,
	 * tags and the Jinja template (sandboxed engine `jinja2-sandbox@1`). CodeMirror-free: a textarea with a
	 * variable autocomplete list (from `GET /prompts/variables/`; ↑/↓ to move, Enter/Tab to insert, Esc to
	 * close), a live render against a sample cell (debounced `POST /prompts/render/` with this draft in
	 * place of its role) and a diff against `base`.
	 *
	 * Props: `value`, `onchange`, `base` (the version being edited; enables the Diff tab), `readonly`,
	 * `renderContext` (the preview cell; default: a sample cell from the library), `variables` (else
	 * loaded), `promptSet` (ref of the set the preview renders inside; default `app-default`/`slot-*`).
	 */
	import { untrack } from 'svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Select from '$lib/components/ui/select';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getPromptVariables, renderPrompts } from '$lib/api/v3/library';
	import type { AppContextFrom, Issue, PromptMode, PromptRole, PromptTemplateContent, PromptVariable, RenderOut } from '$lib/api/v3/types';
	import { PROMPT_ROLES } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import FormRow from './FormRow.svelte';
	import ListInput from './ListInput.svelte';
	import RenderContextPicker from './RenderContextPicker.svelte';
	import StimulusText from './StimulusText.svelte';
	import VersionDiff from './VersionDiff.svelte';
	import { libraryStore } from './library.svelte';
	import { defaultAppContext, defaultPromptSet } from './preview';
	import { applyCompletion, completionList, completionsAt, variableIssues, type Completion, type CompletionContext } from './variables';

	interface Props {
		value: PromptTemplateContent;
		onchange: (v: PromptTemplateContent) => void;
		base?: PromptTemplateContent | null;
		readonly?: boolean;
		renderContext?: AppContextFrom | null;
		variables?: PromptVariable[];
		promptSet?: string | null;
	}

	let { value, onchange, base = null, readonly = false, renderContext = null, variables, promptSet = null }: Props = $props();

	// ── variables ──
	let loadedVars = $state<PromptVariable[]>([]);
	let varsError = $state('');
	$effect(() => {
		if (variables) return;
		getPromptVariables()
			.then((v) => (loadedVars = v))
			.catch((e) => (varsError = formatApiError(e, 'Could not load the template variables.')));
	});
	const allVars = $derived(variables ?? loadedVars);
	const primaryMode = $derived<PromptMode>(value.modes[0] ?? 'app');
	const items = $derived(completionList(allVars, value.modes.length === 1 ? primaryMode : undefined));
	const issues = $derived(variableIssues(value.template, value.variables, allVars, value.modes));

	const set = (patch: Partial<PromptTemplateContent>) => onchange({ ...value, ...patch });

	// ── template text (bound locally so outside updates never move the caret while typing) ──
	let text = $state('');
	$effect.pre(() => {
		const next = value.template;
		untrack(() => {
			if (next !== text) text = next;
		});
	});

	// ── autocomplete ──
	let textarea = $state<HTMLTextAreaElement>();
	let suggestions = $state<{ context: CompletionContext; items: Completion[] } | null>(null);
	let active = $state(0);

	function refreshSuggestions() {
		if (!textarea || readonly) return;
		suggestions = completionsAt(items, textarea.value, textarea.selectionStart);
		if (suggestions && suggestions.items.length === 0) suggestions = null;
		active = 0;
	}

	function accept(item: Completion) {
		if (!textarea || !suggestions) return;
		const next = applyCompletion(textarea.value, textarea.selectionStart, suggestions.context, item);
		text = next.text;
		set({ template: next.text });
		suggestions = null;
		requestAnimationFrame(() => {
			textarea?.focus();
			textarea?.setSelectionRange(next.cursor, next.cursor);
		});
	}

	function keydown(e: KeyboardEvent) {
		if (!suggestions) return;
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			active = (active + 1) % suggestions.items.length;
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			active = (active - 1 + suggestions.items.length) % suggestions.items.length;
		} else if (e.key === 'Enter' || e.key === 'Tab') {
			e.preventDefault();
			accept(suggestions.items[active]);
		} else if (e.key === 'Escape') {
			suggestions = null;
		}
	}

	function insertVariable(name: string) {
		if (readonly || !textarea) return;
		const at = textarea.selectionStart ?? value.template.length;
		const insert = `{{ ${name} }}`;
		text = text.slice(0, at) + insert + text.slice(textarea.selectionEnd ?? at);
		set({ template: text });
	}

	// ── live render ──
	let tab = $state('template');
	const tasks = $derived(libraryStore.list('task'));
	const stacks = $derived(libraryStore.list('stack'));
	const styles = $derived(libraryStore.list('style'));
	const guidance = $derived(libraryStore.list('guidance'));
	const harnesses = $derived(libraryStore.list('harness_preset'));
	const sets = $derived(libraryStore.list('prompt_set'));

	let pickedContext = $state<AppContextFrom | null>(null);
	const sampleContext = $derived(
		renderContext ??
			pickedContext ??
			defaultAppContext({
				tasks: tasks.items,
				stacks: stacks.items,
				styles: styles.items,
				guidance: guidance.items,
				harnessPresets: harnesses.items,
			}),
	);
	const previewSet = $derived(promptSet ?? defaultPromptSet(sets.items, primaryMode)?.pinned_ref ?? null);

	let render = $state<RenderOut | null>(null);
	let renderError = $state('');
	let renderIssues = $state<Issue[]>([]);
	let rendering = $state(false);

	$effect(() => {
		if (tab !== 'preview') return;
		const ctx = sampleContext;
		const template = value.template;
		const role = value.role;
		const setRef = previewSet;
		if (!ctx?.task || role === 'fragment' || primaryMode !== 'app') return;
		const controller = new AbortController();
		const timer = setTimeout(async () => {
			rendering = true;
			renderError = '';
			try {
				const out = await renderPrompts(
					{ prompt_set: setRef, templates: { [role]: { template, variables: untrack(() => value.variables) } }, context_from: ctx },
					{ signal: controller.signal },
				);
				render = out;
				renderIssues = out.issues;
			} catch (e) {
				if (isAbortError(e)) return;
				renderError = formatApiError(e, 'The template did not render.');
				const body = e as { issues?: Issue[] };
				renderIssues = body.issues ?? [];
			} finally {
				if (!controller.signal.aborted) rendering = false;
			}
		}, 500);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	const ownStimulus = $derived(render?.stimuli.find((s) => s.role === value.role) ?? null);
	const roleItems = PROMPT_ROLES.map((r) => ({ value: r, label: r.replace(/_/g, ' ') }));
</script>

<div class="space-y-4">
	<div class="grid gap-4 md:grid-cols-3">
		<FormRow label="Role" for="tpl-role" help="Which stimulus this template renders (fragments are embedded in others).">
			<Select.Simple id="tpl-role" value={value.role} items={roleItems} disabled={readonly} onValueChange={(v) => set({ role: v as PromptRole })} />
		</FormRow>
		<FormRow label="Modes" help="Experiment modes the template may be used in.">
			<div class="flex gap-4 pt-1.5">
				{#each ['app', 'slot'] as const as mode (mode)}
					<label class="flex items-center gap-2 text-sm">
						<Checkbox
							checked={value.modes.includes(mode)}
							disabled={readonly || (value.modes.length === 1 && value.modes[0] === mode)}
							onCheckedChange={(c) => set({ modes: c ? [...new Set([...value.modes, mode])] : value.modes.filter((m) => m !== mode) })}
						/>
						{mode}
					</label>
				{/each}
			</div>
		</FormRow>
		<FormRow label="Tags" for="tpl-tags" help="Free tags (e.g. editor for the aider brief).">
			<ListInput id="tpl-tags" value={value.tags} {readonly} onchange={(tags) => set({ tags })} placeholder="none" />
		</FormRow>
	</div>

	<FormRow label="Declared variables" for="tpl-vars" help="Top-level context variables the template may read. Reading an undeclared one is a lint error.">
		<ListInput id="tpl-vars" value={value.variables} {readonly} onchange={(variables) => set({ variables })} placeholder="task, stack, style" />
		{#if issues.undeclared.length}
			<p class="flex flex-wrap items-center gap-2 text-xs text-danger-fg">
				Used but not declared: <span class="font-mono">{issues.undeclared.join(', ')}</span>
				{#if !readonly}
					<button type="button" class="cursor-pointer text-primary hover:underline" onclick={() => set({ variables: [...value.variables, ...issues.undeclared] })}>Declare them</button>
				{/if}
			</p>
		{/if}
		{#if issues.unknown.length}
			<p class="text-xs text-warning-fg">Not in the documented context for {value.modes.join('/')}: <span class="font-mono">{issues.unknown.join(', ')}</span></p>
		{/if}
	</FormRow>

	<Tabs.Root bind:value={tab}>
		<Tabs.List>
			<Tabs.Trigger value="template">Template</Tabs.Trigger>
			<Tabs.Trigger value="preview">Preview</Tabs.Trigger>
			{#if base}<Tabs.Trigger value="diff">Diff vs base</Tabs.Trigger>{/if}
		</Tabs.List>

		<Tabs.Content value="template" class="pt-3">
			<div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_16rem]">
				<div class="relative">
					<textarea
						bind:this={textarea}
						bind:value={text}
						readonly={readonly}
						spellcheck="false"
						rows={18}
						aria-label="Template"
						aria-autocomplete="list"
						aria-controls="tpl-suggestions"
						class="block min-h-80 w-full rounded-md border border-input bg-surface-1 px-3 py-2 font-mono text-xs leading-relaxed transition-colors hover:border-primary/40 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30 read-only:bg-muted/30"
						oninput={() => {
							set({ template: text });
							refreshSuggestions();
						}}
						onkeydown={keydown}
						onclick={refreshSuggestions}
						onblur={() => setTimeout(() => (suggestions = null), 150)}
					></textarea>
					{#if suggestions}
						<ul
							id="tpl-suggestions"
							role="listbox"
							aria-label="Suggestions"
							class="absolute inset-x-2 bottom-2 z-10 max-h-52 overflow-y-auto rounded-md border border-border bg-popover py-1 text-xs shadow-[var(--shadow-card)]"
						>
							{#each suggestions.items as item, i (item.label)}
								<li role="option" aria-selected={i === active}>
									<button
										type="button"
										class={cn('flex w-full cursor-pointer items-baseline gap-2 px-2 py-1 text-left', i === active ? 'bg-primary/10 text-primary' : 'hover:bg-muted')}
										onmousedown={(e) => {
											e.preventDefault();
											accept(item);
										}}
									>
										<span class="font-mono">{item.label}</span>
										<span class="truncate text-muted-foreground">{item.kind === 'filter' ? 'filter' : item.detail}</span>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				</div>
				<aside class="space-y-2">
					<p class="section-label">Context variables{value.modes.length === 1 ? ` (${primaryMode})` : ''}</p>
					{#if varsError}<p class="text-xs text-danger-fg">{varsError}</p>{/if}
					<ul class="max-h-96 space-y-1.5 overflow-y-auto pr-1">
						{#each allVars.filter((v) => value.modes.some((m) => v.modes.includes(m))) as v (v.name)}
							<li>
								<button
									type="button"
									class="w-full cursor-pointer rounded-md px-1.5 py-1 text-left hover:bg-muted disabled:cursor-default"
									disabled={readonly}
									title={readonly ? v.description : `Insert {{ ${v.name} }}`}
									onclick={() => insertVariable(v.name)}
								>
									<span class="font-mono text-xs">{v.name}</span>
									<span class="ml-1 text-[11px] text-muted-foreground">{v.type}</span>
									<span class="block text-[11px] leading-snug text-muted-foreground">{v.description}</span>
								</button>
							</li>
						{/each}
					</ul>
					<p class="text-[11px] text-muted-foreground">
						Filters: bullet_list, default, endpoint_table, indent, join, length, lower, replace, sort, title, tojson, trim, upper, wordwrap. No numbers from the budget, the seed or any id are ever in the context.
					</p>
				</aside>
			</div>
		</Tabs.Content>

		<Tabs.Content value="preview" class="space-y-3 pt-3">
			{#if value.role === 'fragment'}
				<p class="text-sm text-muted-foreground">Fragments render inside a prompt set. Preview the set that uses this fragment in the <a class="text-primary hover:underline" href="/library/prompts/playground">prompt playground</a>.</p>
			{:else if primaryMode !== 'app'}
				<p class="text-sm text-muted-foreground">Slot templates render against a saved slot target: preview them from the slot picker or the playground.</p>
			{:else}
				{#if !renderContext}
					<details class="rounded-md border border-border px-3 py-2">
						<summary class="cursor-pointer text-sm">Sample cell <span class="text-xs text-muted-foreground">(the levels the preview renders with)</span></summary>
						<div class="pt-3">
							{#if sampleContext}
								<RenderContextPicker value={sampleContext} onchange={(c) => (pickedContext = c)} compact />
							{/if}
						</div>
					</details>
				{/if}
				{#if !sampleContext}
					<p class="text-sm text-muted-foreground">The library has no task to preview with.</p>
				{:else if renderError}
					<ErrorAlert message={renderError} />
				{:else if rendering && !render}
					<LoadingState label="rendering" />
				{/if}
				{#each renderIssues as issue (issue.code + issue.message)}
					<p class="text-xs {issue.severity === 'error' ? 'text-danger-fg' : 'text-warning-fg'}"><span class="font-mono">{issue.code}</span> {issue.message}</p>
				{/each}
				{#if ownStimulus && !renderError}
					<div class={cn(rendering && 'opacity-70 transition-opacity')}>
						<StimulusText stimulus={ownStimulus} />
					</div>
				{/if}
			{/if}
		</Tabs.Content>

		{#if base}
			<Tabs.Content value="diff" class="pt-3">
				<VersionDiff a={base.template} b={value.template} labels={['base', 'draft']} />
			</Tabs.Content>
		{/if}
	</Tabs.Root>
</div>
