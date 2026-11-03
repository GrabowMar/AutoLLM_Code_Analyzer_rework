<script lang="ts">
	/**
	 * SlotTargetForm — the right pane of the slot picker (spec 7.4, 10.5 step 2): the selected part's kind and
	 * label, its hole mode, keep-docstring and context policy (each labelled "default (can be varied in the
	 * study)" while untouched), the edit policy, the allowlist, the linked checks and probes (auto from the
	 * task spec; edits are recorded as `edited` with a diff), a cost model, and the **preview**: hole-applied
	 * text, resolved context files with token counts, leak report, `target_hash`, cost hint and issues.
	 * "Save target" queues the validation (original 3×, holed 1×; machine time only).
	 *
	 * Props: `picker` (`SlotPicker`), `onsaved(target)`.
	 */
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import NumberInput from './NumberInput.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import MousePointerClick from '@lucide/svelte/icons/mouse-pointer-click';
	import Save from '@lucide/svelte/icons/save';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import X from '@lucide/svelte/icons/x';
	import { DEFAULT_CONTEXT_POLICY, type ContextPolicy, type SlotTargetOut } from '$lib/api/v3/types';
	import CodeViewer from '$lib/components/runs/CodeViewer.svelte';
	import { fmtInt, fmtUsd, shortSha } from '$lib/components/runs/format';
	import type { SlotPicker } from './picker.svelte';
	import { modelOptions, type OptionItem } from './options';
	import { selectionTitle, type PartSelection } from './selection';

	interface Props {
		picker: SlotPicker;
		onsaved?: (target: SlotTargetOut) => void;
	}

	let { picker, onsaved }: Props = $props();

	const DEFAULT_TAG = 'default (can be varied in the study)';
	const HOLE_HELP: Record<string, string> = {
		stub: 'Signature and decorators kept; docstring and in-span comments stripped; the body replaced by a neutral placeholder.',
		remove: 'The part (or file) is removed; the prompt describes its role.',
		rewrite: 'The original stays visible and the model rewrites it (measures anchoring; a separate treatment).',
	};
	const CONTEXT_HELP: Record<string, string> = {
		slot_file_only: 'Only the slot file (with the hole).',
		related_files: 'The slot file, its imports (depth 1), model/schema files, the routes file, then by import distance, until the token budget.',
		full_app: 'Every source file of the parent, until the token budget.',
		interface_only: 'Signatures and exported names only.',
	};

	let models = $state<OptionItem[]>([]);
	let newCheck = $state('');
	let newProbe = $state('');
	let newAllow = $state('');

	$effect(() => {
		modelOptions(false)
			.then((items) => (models = items))
			.catch(() => (models = []));
	});

	const selection = $derived(picker.selection);
	const form = $derived(picker.form);
	// `preview` is the preview of exactly the current body (shown in the preview pane, gates Save);
	// `defaults` may be the previous preview of this selection and only labels the form's defaults meanwhile.
	const preview = $derived(picker.preview);
	const defaults = $derived(picker.preview ?? picker.lastPreview);
	const previewIsCurrent = $derived(picker.previewIsCurrent);
	const policy = $derived<ContextPolicy>(form.context_policy ?? defaults?.default_context_policy ?? DEFAULT_CONTEXT_POLICY);
	const holeMode = $derived(form.hole_mode ?? defaults?.default_hole_mode ?? null);
	const keepDocstring = $derived(form.keep_docstring ?? defaults?.default_keep_docstring ?? false);
	const allowlist = $derived(form.allowlist ?? defaults?.allowlist ?? (selection ? [selection.path] : []));
	const shownChecks = $derived(
		[...new Set([...(defaults?.linked_checks ?? []), ...(defaults?.linked_checks_diff.removed ?? []), ...(form.linked_checks ?? [])])].sort(),
	);
	const activeChecks = $derived(new Set(form.linked_checks ?? defaults?.linked_checks ?? []));
	const shownProbes = $derived([...new Set([...(defaults?.linked_probes ?? []), ...(form.linked_probes ?? [])])].sort());
	const activeProbes = $derived(new Set(form.linked_probes ?? defaults?.linked_probes ?? []));
	const blocking = $derived((preview?.issues ?? []).filter((i) => i.severity === 'error'));
	const pathItems = $derived(picker.tree.filter((t) => !t.excluded).map((t) => ({ value: t.path, label: t.path })));

	function setPolicy(change: Partial<ContextPolicy>) {
		picker.updateForm({ context_policy: { ...policy, ...change, related: { ...policy.related, ...(change.related ?? {}) } } });
	}

	function toggleCheck(id: string, on: boolean) {
		const next = new Set(activeChecks);
		if (on) next.add(id);
		else next.delete(id);
		picker.updateForm({ linked_checks: [...next] });
	}

	function toggleProbe(id: string, on: boolean) {
		const next = new Set(activeProbes);
		if (on) next.add(id);
		else next.delete(id);
		picker.updateForm({ linked_probes: [...next] });
	}

	function changeKind(next: PartSelection['kind']) {
		const s = selection;
		if (!s || s.kind === next) return;
		if (next === 'file') picker.select({ kind: 'file', path: s.path });
		else if (next === 'region') {
			const lines = picker.selectedLines ?? { start_line: 1, end_line: 1 };
			picker.select({ kind: 'region', path: s.path, ...lines });
		}
	}

	function setRegionLine(which: 'start_line' | 'end_line', value: number) {
		const s = selection;
		if (!s || s.kind !== 'region' || !Number.isFinite(value)) return;
		picker.select({ ...s, [which]: Math.max(1, Math.round(value)) });
	}

	async function save() {
		const target = await picker.save();
		if (target) {
			onsaved?.(target);
			picker.clearSelection();
		}
	}

	function isDefault(field: 'default_hole_mode' | 'default_keep_docstring' | 'default_context_policy'): boolean {
		const key = field === 'default_hole_mode' ? 'hole_mode' : field === 'default_keep_docstring' ? 'keep_docstring' : 'context_policy';
		return form[key] === null;
	}
</script>

{#snippet defaultTag(field: 'default_hole_mode' | 'default_keep_docstring' | 'default_context_policy', reset: () => void)}
	{#if isDefault(field)}
		<span class="text-[11px] text-muted-foreground" title="The kind default; a study factor can vary it per condition">{DEFAULT_TAG}</span>
	{:else}
		<button type="button" class="inline-flex cursor-pointer items-center gap-1 text-[11px] text-primary hover:underline" onclick={reset}>
			<RotateCcw class="h-3 w-3" />use the default
		</button>
	{/if}
{/snippet}

{#if !selection}
	<EmptyState
		compact
		icon={MousePointerClick}
		title="Select a part"
		description="Pick an endpoint or component on the left, a symbol chip in the code, or drag over line numbers for a region."
	/>
{:else}
	<div class="space-y-4">
		<div class="space-y-2">
			<div class="flex flex-wrap items-center gap-2">
				<Badge variant="default">{selection.kind}</Badge>
				<span class="min-w-0 truncate font-mono text-xs" title={selectionTitle(selection)}>{selectionTitle(selection)}</span>
				<button type="button" class="ml-auto cursor-pointer text-muted-foreground hover:text-foreground" aria-label="Clear the selection" onclick={() => picker.clearSelection()}>
					<X class="h-4 w-4" />
				</button>
			</div>
			<div class="flex flex-wrap gap-1 text-[11px]">
				{#if selection.kind !== 'file'}
					<button type="button" class="cursor-pointer rounded border border-border px-1.5 py-0.5 hover:border-primary/60" onclick={() => changeKind('file')}>use the whole file</button>
				{/if}
				{#if selection.kind !== 'region'}
					<button type="button" class="cursor-pointer rounded border border-border px-1.5 py-0.5 hover:border-primary/60" onclick={() => changeKind('region')}>as a line region</button>
				{/if}
			</div>
			{#if selection.kind === 'region'}
				<div class="grid grid-cols-2 gap-2">
					<div class="space-y-1">
						<Label for="region-start">From line</Label>
						<NumberInput id="region-start" min={1} step={1} value={selection.start_line} onvaluechange={(v) => v !== null && setRegionLine('start_line', v)} />
					</div>
					<div class="space-y-1">
						<Label for="region-end">To line</Label>
						<NumberInput id="region-end" min={1} step={1} value={selection.end_line} onvaluechange={(v) => v !== null && setRegionLine('end_line', v)} />
					</div>
				</div>
			{/if}
			<div class="space-y-1">
				<Label for="target-label">Label</Label>
				<Input id="target-label" placeholder="generated from the part when empty" value={form.label} oninput={(e) => picker.updateForm({ label: (e.currentTarget as HTMLInputElement).value })} />
			</div>
		</div>

		<SectionHeading size="sm" title="Treatment defaults" />
		<div class="space-y-3">
			<div class="space-y-1">
				<div class="flex items-center justify-between gap-2">
					<Label for="hole-mode">Hole mode</Label>
					{@render defaultTag('default_hole_mode', () => picker.resetField('hole_mode'))}
				</div>
				<Select.Simple
					id="hole-mode"
					value={holeMode ?? ''}
					onValueChange={(v) => picker.updateForm({ hole_mode: (v || null) as typeof form.hole_mode })}
					placeholder="kind default"
					items={[
						{ value: 'stub', label: 'Stub', description: HOLE_HELP.stub },
						{ value: 'remove', label: 'Remove', description: HOLE_HELP.remove },
						{ value: 'rewrite', label: 'Rewrite', description: HOLE_HELP.rewrite },
					]}
				/>
			</div>
			<div class="space-y-1">
				<div class="flex items-center justify-between gap-2">
					<label class="flex items-center gap-2 text-sm">
						<Checkbox checked={keepDocstring} onCheckedChange={(v) => picker.updateForm({ keep_docstring: v === true })} />
						Keep the docstring
					</label>
					{@render defaultTag('default_keep_docstring', () => picker.resetField('keep_docstring'))}
				</div>
			</div>
			<div class="space-y-2 rounded-md border border-border p-2.5">
				<div class="flex items-center justify-between gap-2">
					<span class="text-sm font-medium">Context policy</span>
					{@render defaultTag('default_context_policy', () => picker.resetField('context_policy'))}
				</div>
				<Select.Simple
					aria-label="Context mode"
					value={policy.mode}
					onValueChange={(v) => setPolicy({ mode: v as ContextPolicy['mode'] })}
					items={Object.entries(CONTEXT_HELP).map(([value, description]) => ({ value, label: value.replace(/_/g, ' '), description }))}
				/>
				<div class="grid grid-cols-2 gap-2">
					<div class="space-y-1">
						<Label for="ctx-tokens">Max context tokens</Label>
<NumberInput
							id="ctx-tokens"
							min={1000}
							step={1000}
							value={policy.max_context_tokens}
							onvaluechange={(v) => {
								if (v !== null && v > 0) setPolicy({ max_context_tokens: Math.round(v) });
							}}
						/>
					</div>
					<div class="space-y-1">
						<Label for="ctx-spec">Spec prose</Label>
						<Select.Simple
							id="ctx-spec"
							value={policy.include_spec}
							onValueChange={(v) => setPolicy({ include_spec: v as ContextPolicy['include_spec'] })}
							items={[
								{ value: 'none', label: 'None' },
								{ value: 'endpoint', label: 'Linked endpoints' },
								{ value: 'full', label: 'Full task' },
							]}
						/>
					</div>
				</div>
				<label class="flex items-center gap-2 text-xs">
					<Checkbox checked={policy.include_tests} onCheckedChange={(v) => setPolicy({ include_tests: v === true })} />
					Include the parent's own tests (never hidden checks)
				</label>
			</div>
		</div>

		<SectionHeading size="sm" title="Edit scope" />
		<div class="space-y-3">
			<div class="space-y-1">
				<Label for="edit-policy">Out-of-slot edits</Label>
				<Select.Simple
					id="edit-policy"
					value={form.edit_policy ?? defaults?.edit_policy ?? 'revert'}
					onValueChange={(v) => picker.updateForm({ edit_policy: v as 'revert' | 'fail' })}
					items={[
						{ value: 'revert', label: 'Revert them', description: 'Edits outside the allowlist are reverted and recorded' },
						{ value: 'fail', label: 'Fail the variant', description: 'Any edit outside the allowlist makes the variant output_invalid' },
					]}
				/>
			</div>
			<div class="space-y-1">
				<span class="section-label">Allowlist</span>
				<div class="flex flex-wrap gap-1">
					{#each allowlist as path (path)}
						<span class="inline-flex items-center gap-1 rounded border border-border px-1.5 py-0.5 font-mono text-[11px]">
							{path}
							{#if path !== selection.path}
								<button type="button" class="cursor-pointer text-muted-foreground hover:text-foreground" aria-label={`Remove ${path}`} onclick={() => picker.updateForm({ allowlist: allowlist.filter((p) => p !== path) })}>
									<X class="h-3 w-3" />
								</button>
							{/if}
						</span>
					{/each}
				</div>
				<div class="flex gap-1.5">
					<Select.Simple size="sm" aria-label="Add a path to the allowlist" bind:value={newAllow} placeholder="Add a path" items={pathItems.filter((p) => !allowlist.includes(p.value))} />
					<Button variant="outline" size="sm" disabled={!newAllow} onclick={() => { picker.updateForm({ allowlist: [...allowlist, newAllow] }); newAllow = ''; }}>Add</Button>
				</div>
			</div>
		</div>

		<SectionHeading size="sm" title="Linked checks and probes" count={activeChecks.size} />
		<div class="space-y-3 text-xs">
			<p class="text-muted-foreground">
				Validation keeps only checks that pass 3/3 on the original and fail with the hole.
				{#if defaults?.linked_checks_source === 'edited'}<Badge variant="secondary">edited</Badge>{/if}
			</p>
			{#if shownChecks.length === 0}
				<p class="text-muted-foreground">No check is linked to this part{defaults ? '' : ' (preview to see the automatic links)'}.</p>
			{/if}
			<ul class="space-y-1">
				{#each shownChecks as id (id)}
					<li>
						<label class="flex items-center gap-2">
							<Checkbox checked={activeChecks.has(id)} onCheckedChange={(v) => toggleCheck(id, v === true)} />
							<span class="font-mono">{id}</span>
							{#if defaults?.linked_checks_diff.added.includes(id)}<span class="text-muted-foreground">added</span>{/if}
						</label>
					</li>
				{/each}
			</ul>
			<form class="flex gap-1.5" onsubmit={(e) => { e.preventDefault(); if (newCheck.trim()) { toggleCheck(newCheck.trim(), true); newCheck = ''; } }}>
				<Input class="h-8" placeholder="Add a check id" aria-label="Add a check id" bind:value={newCheck} />
				<Button variant="outline" size="sm" type="submit" disabled={!newCheck.trim()}>Add</Button>
			</form>
			<p class="section-label">Probes</p>
			{#if shownProbes.length === 0}<p class="text-muted-foreground">No probe on the linked endpoints.</p>{/if}
			<ul class="space-y-1">
				{#each shownProbes as id (id)}
					<li>
						<label class="flex items-center gap-2">
							<Checkbox checked={activeProbes.has(id)} onCheckedChange={(v) => toggleProbe(id, v === true)} />
							<span class="font-mono">{id}</span>
						</label>
					</li>
				{/each}
			</ul>
			<form class="flex gap-1.5" onsubmit={(e) => { e.preventDefault(); if (newProbe.trim()) { toggleProbe(newProbe.trim(), true); newProbe = ''; } }}>
				<Input class="h-8" placeholder="Add a probe id" aria-label="Add a probe id" bind:value={newProbe} />
				<Button variant="outline" size="sm" type="submit" disabled={!newProbe.trim()}>Add</Button>
			</form>
		</div>

		<SectionHeading size="sm" title="Preview" />
		<div class="space-y-3">
			<div class="flex flex-wrap items-center gap-2">
				<Select.Simple
					size="sm"
					aria-label="Model for the cost hint"
					value={form.cost_model ?? ''}
					onValueChange={(v) => picker.updateForm({ cost_model: v || null })}
					placeholder="Model for the cost hint"
					clearLabel="No cost hint"
					items={models}
				/>
				<Button variant="outline" size="sm" disabled={picker.previewLoading} onclick={() => picker.runPreview()}>
					{#if picker.previewLoading}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{/if}
					Preview
				</Button>
			</div>
			{#if picker.previewError}<ErrorAlert message={picker.previewError} />{/if}
			{#if preview}
				{#if preview.issues.length}
					<ul class="space-y-1 text-xs">
						{#each preview.issues as issue, i (i)}
							<li class="flex items-start gap-1.5">
								<StatusBadge status={issue.severity} tone={issue.severity === 'error' ? 'danger' : issue.severity === 'warn' ? 'warning' : 'neutral'} label={issue.code} class="normal-case" />
								<span>{issue.message}</span>
							</li>
						{/each}
					</ul>
				{/if}
				<Card.Root class="gap-0 py-0">
					<Card.Content class="space-y-2 py-3 text-xs">
						<div class="flex flex-wrap gap-x-4 gap-y-1">
							<span>hole <b>{preview.default_hole_mode}</b>{preview.defaults.includes('default_hole_mode') ? ' (default)' : ''}</span>
							<span>language <b>{preview.language || '—'}</b></span>
							{#if preview.match_key}<span title="Used to find the same part in other parents">match <span class="font-mono">{preview.match_key}</span></span>{/if}
							<span title={preview.target_hash}>target <span class="font-mono">{shortSha(preview.target_hash)}</span></span>
						</div>
						<div class="flex flex-wrap items-center gap-2">
							<span class="section-label">Leak</span>
							<StatusBadge
								status={preview.leak.matches.some((m) => m.verbatim) ? 'error' : preview.leak.score > 0.3 ? 'warning' : 'completed'}
								label={`score ${preview.leak.score.toFixed(2)}`}
							/>
							<span class="text-muted-foreground">share of the original's 8-token shingles found in the context{preview.leak.scope.includes('workspace') ? ' and agent workspace' : ''}</span>
						</div>
						{#if preview.leak.matches.length}
							<ul class="ml-2 space-y-0.5">
								{#each preview.leak.matches as m (m.path)}
									<li class="font-mono">{m.path} · {m.shingles} shingles{#if m.verbatim} · <b class="text-danger-fg">verbatim copy</b>{/if}</li>
								{/each}
							</ul>
						{/if}
						<div>
							<span class="section-label">Cost hint</span>
							{#if preview.cost_hint.per_variant_usd !== null}
								<p>≈ {fmtUsd(preview.cost_hint.per_variant_usd)} per variant · 30 variants ≈ {fmtUsd(preview.cost_hint.per_30_usd)} <span class="text-muted-foreground">({preview.cost_hint.model}; {fmtInt(preview.cost_hint.tokens_in)} in / {fmtInt(preview.cost_hint.tokens_out)} out tokens, single shot)</span></p>
							{:else}
								<p class="text-muted-foreground">≈ {fmtInt(preview.cost_hint.tokens_in)} in / {fmtInt(preview.cost_hint.tokens_out)} out tokens per variant{preview.cost_hint.model ? ` (no price for ${preview.cost_hint.model})` : ' (pick a model for a price)'}.</p>
							{/if}
							<p class="text-muted-foreground">Validation runs (original 3×, holed 1×) cost machine time only.</p>
						</div>
					</Card.Content>
				</Card.Root>
				<div class="space-y-1">
					<p class="section-label">Resolved context · {fmtInt(preview.resolved_context.tokens)} tokens</p>
					<ul class="space-y-0.5 text-xs">
						{#each preview.resolved_context.files as f (f.path)}
							<li class="flex gap-2"><span class="min-w-0 flex-1 truncate font-mono">{f.path}</span><span class="tabular-nums text-muted-foreground">{fmtInt(f.tokens)}</span></li>
						{/each}
					</ul>
					{#if preview.resolved_context.truncated_paths.length}
						<p class="text-[11px] text-muted-foreground">Left out by the token budget: <span class="font-mono">{preview.resolved_context.truncated_paths.join(', ')}</span></p>
					{/if}
				</div>
				<div class="space-y-1">
					<p class="section-label">With the hole applied{preview.hole_removes_file ? ' (the file is removed)' : ''}</p>
					{#if preview.hole_removes_file}
						<p class="text-xs text-muted-foreground">The whole file is removed; the prompt describes its role.</p>
					{:else}
						<CodeViewer code={preview.hole_applied_text} language={preview.language} path={selection.path} class="max-h-80" />
					{/if}
				</div>
			{:else if !picker.previewLoading && !picker.previewError}
				<p class="text-xs text-muted-foreground">The preview updates as you edit.</p>
			{/if}
		</div>

		{#if picker.saveError}<ErrorAlert message={picker.saveError} />{/if}
		<div class="sticky bottom-0 flex items-center gap-2 border-t border-border bg-card py-2">
			{#if blocking.length}<span class="text-xs text-danger-fg">{blocking.length} blocking issue{blocking.length === 1 ? '' : 's'}</span>{/if}
			<Button
				size="sm"
				class="ml-auto"
				disabled={picker.saving || !previewIsCurrent || blocking.length > 0}
				onclick={save}
				title={!previewIsCurrent ? (picker.previewLoading ? 'Waiting for the preview of this part' : 'Preview the part first') : undefined}
			>
				{#if picker.saving}<LoaderCircle class="mr-1.5 h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />{:else}<Save class="mr-1.5 h-3.5 w-3.5" />{/if}
				Save target
			</Button>
		</div>
	</div>
{/if}
