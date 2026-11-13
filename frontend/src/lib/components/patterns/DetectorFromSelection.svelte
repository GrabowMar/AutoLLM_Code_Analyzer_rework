<script lang="ts">
	/**
	 * DetectorFromSelection — "Create detector from selection" (spec 7.9, 9.7): turns selected lines of a
	 * variant's (or a cluster representative's) file into a `regex` or `ts_query` detector draft
	 * (`POST /patterns/detectors/from-selection/`), lets the researcher edit the rule, then saves it as a new
	 * library detector (v1, private) through the library API. A saved detector is used by the next PatternRun;
	 * detectors report presence only and copy the tool's own label, never a computed severity.
	 *
	 * Props: `open` (bindable), `runId`, `path`, `span` (`{start_line, end_line}`), `language`.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { formatApiError } from '$lib/api/core';
	import { createLibraryItem } from '$lib/api/v3/library';
	import { draftDetectorFromSelection } from '$lib/api/v3/patterns';
	import type { DetectorContent, DetectorPolarity } from '$lib/api/v3/types';

	interface Props {
		open?: boolean;
		runId: string;
		path: string;
		span: { start_line: number; end_line: number } | null;
		language?: string;
	}

	let { open = $bindable(false), runId, path, span, language = '' }: Props = $props();

	const SLUG_RE = /^[a-z0-9][a-z0-9_-]*$/;

	let engine = $state<'regex' | 'ts_query'>('regex');
	let featureKey = $state('');
	let polarity = $state<DetectorPolarity>('insecure');
	let draft = $state<DetectorContent | null>(null);
	let rule = $state('');
	let slug = $state('');
	let name = $state('');
	let busy = $state(false);
	let error = $state('');
	let saved = $state<string | null>(null);

	$effect(() => {
		if (!open) return;
		// a new selection starts a new draft
		void [runId, path, span?.start_line, span?.end_line];
		draft = null;
		saved = null;
		error = '';
	});

	const featureValid = $derived(SLUG_RE.test(featureKey));
	const slugValid = $derived(SLUG_RE.test(slug));

	async function makeDraft() {
		if (!span) return;
		busy = true;
		error = '';
		try {
			const out = await draftDetectorFromSelection({
				run_id: runId,
				path,
				span: { start_line: span.start_line, end_line: span.end_line },
				engine,
				feature_key: featureKey,
				polarity,
			});
			draft = out.draft;
			rule = out.draft.rule;
			if (!slug) slug = featureKey.replace(/_/g, '-');
			if (!name) name = featureKey.replace(/[_-]/g, ' ');
		} catch (e) {
			error = formatApiError(e, 'Could not draft a detector from this selection.');
		} finally {
			busy = false;
		}
	}

	async function save() {
		if (!draft) return;
		busy = true;
		error = '';
		try {
			const item = await createLibraryItem('detectors', {
				slug,
				name: name || slug,
				description: `Drafted from ${path}:${span?.start_line}-${span?.end_line} of run ${runId.slice(0, 8)}.`,
				content: { ...draft, rule },
				visibility: 'private',
			});
			saved = item.pinned_ref;
		} catch (e) {
			error = formatApiError(e, 'Could not save the detector.');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-2xl">
		<Dialog.Header>
			<Dialog.Title>Create a detector from the selection</Dialog.Title>
			<Dialog.Description>
				<span class="font-mono">{path}</span>{#if span}, lines {span.start_line}–{span.end_line}{/if}{#if language} · {language}{/if}.
				The draft is a starting point: generalise the rule before saving.
			</Dialog.Description>
		</Dialog.Header>

		{#if saved}
			<div class="space-y-3 text-sm">
				<p>Saved as <span class="font-mono">{saved}</span>. Re-run patterns with it (no LLM cost) to measure the feature.</p>
				<Dialog.Footer>
					<Button variant="outline" size="sm" href={`/library/detectors/${encodeURIComponent(slug)}`}>Open in the Library</Button>
					<Button size="sm" onclick={() => (open = false)}>Done</Button>
				</Dialog.Footer>
			</div>
		{:else}
			<div class="space-y-3">
				<div class="grid gap-3 sm:grid-cols-3">
					<div class="space-y-1">
						<Label>Engine</Label>
						<Segmented
							size="sm"
							aria-label="Detector engine"
							bind:value={engine}
							items={[
								{ value: 'regex', label: 'Regex' },
								{ value: 'ts_query', label: 'Tree-sitter', title: 'A tree-sitter query over the parsed span' },
							]}
						/>
					</div>
					<div class="space-y-1">
						<Label for="det-feature">Feature key</Label>
						<Input id="det-feature" placeholder="sql_string_concat" bind:value={featureKey} aria-invalid={!!featureKey && !featureValid} />
					</div>
					<div class="space-y-1">
						<Label>Polarity</Label>
						<Segmented
							size="sm"
							aria-label="Polarity"
							value={polarity}
							onValueChange={(v) => (polarity = v as DetectorPolarity)}
							items={[
								{ value: 'insecure', label: 'Insecure' },
								{ value: 'secure', label: 'Secure' },
								{ value: 'neutral', label: 'Neutral' },
							]}
						/>
					</div>
				</div>
				{#if featureKey && !featureValid}<p class="text-[11px] text-danger-fg">Lowercase letters, digits, - and _ only.</p>{/if}
				<Button variant="outline" size="sm" disabled={busy || !span || !featureValid} onclick={makeDraft}>
					{#if busy && !draft}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{/if}
					{draft ? 'Draft again' : 'Draft the detector'}
				</Button>

				{#if draft}
					<div class="space-y-1">
						<Label for="det-rule">Rule ({draft.engine})</Label>
						<Textarea id="det-rule" class="min-h-32 font-mono text-xs" bind:value={rule} />
						<p class="text-[11px] text-muted-foreground">
							languages {draft.languages.join(', ') || 'any'} · scopes {draft.scopes.join(', ')} · {draft.polarity}
						</p>
					</div>
					<div class="grid gap-3 sm:grid-cols-2">
						<div class="space-y-1">
							<Label for="det-slug">Library slug</Label>
							<Input id="det-slug" bind:value={slug} aria-invalid={!!slug && !slugValid} />
						</div>
						<div class="space-y-1">
							<Label for="det-name">Name</Label>
							<Input id="det-name" bind:value={name} />
						</div>
					</div>
				{/if}
				{#if error}<ErrorAlert message={error} />{/if}
				<Dialog.Footer>
					<Button variant="outline" size="sm" onclick={() => (open = false)}>Cancel</Button>
					{#if draft}
						<Button
							variant="outline"
							size="sm"
							href={`/library/detectors/new?prefill=${encodeURIComponent(JSON.stringify({ ...draft, rule }))}`}
							target="_blank"
							title="Open the full detector editor with this draft (CWE, scopes, tool label field…)"
						>
							Open in the editor
						</Button>
					{/if}
					<Button size="sm" disabled={busy || !draft || !slugValid || !rule.trim()} onclick={save}>
						{#if busy && draft}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{/if}
						Save to the Library
					</Button>
				</Dialog.Footer>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
