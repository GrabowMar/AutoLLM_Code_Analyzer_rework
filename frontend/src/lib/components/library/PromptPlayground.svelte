<script lang="ts">
	/**
	 * PromptPlayground — render a prompt set against any app-mode cell (`POST /prompts/render/`) and
	 * compare two renders (`POST /prompts/diff/`). Each stimulus shows its text with source spans by owner,
	 * its size and sha, and the render's `prompt_hash`; budget, seed and ids never enter the context, so
	 * two cells that differ only in their Budget preset render identically.
	 *
	 * Props: `initial` (the first cell; default: a sample from the library), `initialSet` (prompt-set ref).
	 */
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Tabs from '$lib/components/ui/tabs';
	import * as Card from '$lib/components/ui/card';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Copy from '@lucide/svelte/icons/copy';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { diffPrompts, renderPrompts } from '$lib/api/v3/library';
	import type { AppContextFrom, Issue, PromptDiffOut, RenderIn, RenderOut } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import LibraryRefPicker from './LibraryRefPicker.svelte';
	import RenderContextPicker from './RenderContextPicker.svelte';
	import StimulusText from './StimulusText.svelte';
	import VersionDiff from './VersionDiff.svelte';
	import { libraryStore } from './library.svelte';
	import { defaultAppContext, defaultPromptSet } from './preview';

	interface Props {
		initial?: AppContextFrom | null;
		initialSet?: string | null;
	}

	let { initial = null, initialSet = null }: Props = $props();

	const tasks = libraryStore.list('task');
	const stacks = libraryStore.list('stack');
	const styles = libraryStore.list('style');
	const guidance = libraryStore.list('guidance');
	const harnesses = libraryStore.list('harness_preset');
	const sets = libraryStore.list('prompt_set');

	let ctxA = $state<AppContextFrom | null>(null);
	let ctxB = $state<AppContextFrom | null>(null);
	let setA = $state<string>('');
	let setB = $state<string>('');
	let compare = $state(false);

	// Fill the defaults once the lists arrive.
	$effect(() => {
		if (!ctxA) {
			ctxA =
				initial ??
				defaultAppContext({ tasks: tasks.items, stacks: stacks.items, styles: styles.items, guidance: guidance.items, harnessPresets: harnesses.items });
		}
		if (!setA) setA = initialSet ?? defaultPromptSet(sets.items, 'app')?.pinned_ref ?? '';
	});

	function startCompare(on: boolean) {
		compare = on;
		if (on && !ctxB && ctxA) {
			ctxB = { ...ctxA, variant: ctxA.variant === 'minimal' ? 'standard' : 'minimal' };
			setB = setA;
		}
	}

	const reqA = $derived<RenderIn | null>(ctxA?.task && setA ? { prompt_set: setA, context_from: ctxA } : null);
	const reqB = $derived<RenderIn | null>(compare && ctxB?.task && setB ? { prompt_set: setB, context_from: ctxB } : null);

	let outA = $state<RenderOut | null>(null);
	let outB = $state<RenderOut | null>(null);
	let diff = $state<PromptDiffOut | null>(null);
	let errorA = $state('');
	let errorB = $state('');
	let issuesA = $state<Issue[]>([]);
	let loading = $state(false);

	function failure(e: unknown): { message: string; issues: Issue[] } {
		return { message: formatApiError(e, 'The prompt set did not render.'), issues: (e as { issues?: Issue[] }).issues ?? [] };
	}

	$effect(() => {
		const a = reqA;
		const b = reqB;
		if (!a) return;
		const controller = new AbortController();
		const timer = setTimeout(async () => {
			loading = true;
			errorA = '';
			errorB = '';
			try {
				outA = await renderPrompts(a, { signal: controller.signal });
				issuesA = outA.issues;
			} catch (e) {
				if (isAbortError(e)) return;
				const f = failure(e);
				errorA = f.message;
				issuesA = f.issues;
				outA = null;
			}
			if (b) {
				try {
					[outB, diff] = await Promise.all([renderPrompts(b, { signal: controller.signal }), diffPrompts(a, b, { signal: controller.signal })]);
				} catch (e) {
					if (isAbortError(e)) return;
					errorB = failure(e).message;
					outB = null;
					diff = null;
				}
			} else {
				outB = null;
				diff = null;
			}
			if (!controller.signal.aborted) loading = false;
		}, 400);
		return () => {
			clearTimeout(timer);
			controller.abort();
		};
	});

	let role = $state('');
	const roles = $derived(outA?.stimuli.map((s) => s.role) ?? []);
	$effect(() => {
		if (roles.length && !roles.includes(role)) role = roles[0];
	});
	const stimulusA = $derived(outA?.stimuli.find((s) => s.role === role) ?? null);
	const stimulusB = $derived(outB?.stimuli.find((s) => s.role === role) ?? null);
	const roleDiff = $derived(diff?.per_role.find((d) => d.role === role) ?? null);
	const sameHash = $derived(!!outA && !!outB && outA.prompt_hash === outB.prompt_hash);

	const loadError = $derived(tasks.error || sets.error);
</script>

{#snippet cellEditor(label: string, ctx: AppContextFrom, setRef: string, onctx: (c: AppContextFrom) => void, onset: (r: string) => void)}
	<Card.Root class="gap-3 py-4">
		<Card.Header class="px-4">
			<Card.Title class="text-base">{label}</Card.Title>
		</Card.Header>
		<Card.Content class="space-y-3 px-4">
			<div class="space-y-1">
				<span class="section-label">Prompt set</span>
				<LibraryRefPicker kind="prompt_set" value={setRef ? [setRef] : []} filter={(i) => (i.content as { mode?: string }).mode === 'app'} onchange={(r) => onset(r[0] ?? '')} aria-label="{label}: prompt set" />
			</div>
			<RenderContextPicker value={ctx} onchange={onctx} compact />
		</Card.Content>
	</Card.Root>
{/snippet}

<div class="space-y-4">
	{#if loadError}<ErrorAlert message={loadError} />{/if}

	<div class="flex flex-wrap items-center justify-between gap-2">
		<label class="flex items-center gap-2 text-sm">
			<Checkbox checked={compare} onCheckedChange={(c) => startCompare(c === true)} /> Compare with a second cell
		</label>
		{#if outA}
			<p class="flex items-center gap-2 font-mono text-[11px] text-muted-foreground">
				prompt_hash {outA.prompt_hash.slice(0, 16)}
				<Button variant="ghost" size="icon" class="size-6" aria-label="Copy prompt hash" onclick={() => navigator.clipboard?.writeText(outA!.prompt_hash)}><Copy class="size-3.5" /></Button>
				{#if outB}<span>· B {outB.prompt_hash.slice(0, 16)} {sameHash ? '(identical)' : ''}</span>{/if}
			</p>
		{/if}
	</div>

	{#if ctxA}
		<div class={cn('grid gap-4', compare && 'xl:grid-cols-2')}>
			{@render cellEditor(compare ? 'Cell A' : 'Cell', ctxA, setA, (c) => (ctxA = c), (r) => (setA = r))}
			{#if compare && ctxB}
				{@render cellEditor('Cell B', ctxB, setB, (c) => (ctxB = c), (r) => (setB = r))}
			{/if}
		</div>
	{:else if tasks.loaded}
		<EmptyState title="No task to render" description="Seed the library (manage.py seed_library) or create a task first." />
	{:else}
		<LoadingState label="loading the library" />
	{/if}

	{#if errorA}<ErrorAlert message={errorA} />{/if}
	{#each issuesA as issue (issue.code + issue.message)}
		<p class="text-xs {issue.severity === 'error' ? 'text-danger-fg' : 'text-warning-fg'}"><span class="font-mono">{issue.code}</span> {issue.message}</p>
	{/each}
	{#if errorB}<ErrorAlert message="Cell B: {errorB}" />{/if}

	{#if outA && roles.length}
		<Tabs.Root bind:value={role}>
			<Tabs.List>
				{#each roles as r (r)}
					<Tabs.Trigger value={r}>{r.replace(/_/g, ' ')}</Tabs.Trigger>
				{/each}
				{#if loading}<span class="ml-auto text-[11px] text-muted-foreground">rendering…</span>{/if}
			</Tabs.List>
			{#each roles as r (r)}
				<Tabs.Content value={r} class="pt-3">
					{#if compare && outB}
						{#if roleDiff}
							<VersionDiff unified={roleDiff.unified} labels={['A', 'B']} />
						{:else if stimulusA && stimulusB && stimulusA.text === stimulusB.text}
							<p class="text-sm text-muted-foreground">The {r} text is identical in both cells.</p>
						{/if}
						<div class="mt-4 grid gap-4 xl:grid-cols-2">
							{#if stimulusA}<div class="min-w-0"><p class="section-label pb-1">A</p><StimulusText stimulus={stimulusA} /></div>{/if}
							{#if stimulusB}<div class="min-w-0"><p class="section-label pb-1">B</p><StimulusText stimulus={stimulusB} /></div>{/if}
						</div>
					{:else if stimulusA}
						<StimulusText stimulus={stimulusA} />
					{/if}
				</Tabs.Content>
			{/each}
		</Tabs.Root>
	{/if}
</div>
