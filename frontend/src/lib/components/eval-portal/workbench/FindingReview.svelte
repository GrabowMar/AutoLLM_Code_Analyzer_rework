<script lang="ts">
	/**
	 * FindingReview — automated analysis findings sampled for this app, each
	 * judged as a real issue (TP), a false alarm (FP) or unsure, with a note.
	 * A finding shows only what its tool printed: the tool, the tool's own level
	 * (`tool_severity`, "unrated" when the tool rates nothing — never a
	 * normalized severity), rule, location, snippet, evidence and docs link.
	 * Verdicts save at once; notes save 800 ms after typing stops. The saver is
	 * the workbench's (`saver`), so submit, skip, hiding the tab and leaving
	 * wait for every verdict and note, and its errors (read-only, session
	 * ended) are handled there.
	 *
	 * Props: `saver` (BlockAutosave keyed by finding id), `findingsBySlot`,
	 * `slotLabels`, `verdicts`, `readOnly`, `canOpenSource(slot)`,
	 * `onverdict(id, entry)`, `onopensource(slot, path, line)`.
	 */
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import FileCode from '@lucide/svelte/icons/file-code';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import type { FindingVerdictValue, ReviewFinding } from '$lib/api/types/human-eval';
	import LevelBadge from '$lib/components/analysis/LevelBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { Textarea } from '$lib/components/ui/textarea';
	import ChoiceGroup, { type ChoiceOption } from './ChoiceGroup.svelte';
	import type { BlockAutosave } from './autosave.svelte';
	import { safeHttpUrl } from './hosting';
	import type { Slot } from './workbench';

	type Entry = { verdict: FindingVerdictValue; note: string };

	interface Props {
		saver: BlockAutosave<Entry>;
		findingsBySlot: Record<string, ReviewFinding[]>;
		slotLabels: Partial<Record<string, string>>;
		verdicts: Record<string, Entry>;
		readOnly?: boolean;
		canOpenSource?: (slot: Slot) => boolean;
		onverdict: (findingId: string, entry: Entry) => void;
		onopensource?: (slot: Slot, path: string, line: number | null) => void;
	}

	let {
		saver,
		findingsBySlot,
		slotLabels,
		verdicts,
		readOnly = false,
		canOpenSource = () => false,
		onverdict,
		onopensource,
	}: Props = $props();

	const uid = $props.id();

	const VERDICTS: ChoiceOption[] = [
		{ value: 'tp', label: 'Real issue', title: 'True positive: the problem the tool describes is real in this app' },
		{ value: 'fp', label: 'False alarm', title: 'False positive: there is no such problem here' },
		{ value: 'unsure', label: 'Unsure', tone: 'neutral', title: 'You cannot tell' },
	];

	/** Notes typed but not saved yet (the verdict is required to save one). */
	let notes = $state<Record<string, string>>({});
	let expanded = $state<Record<string, boolean>>({});

	const sections = $derived(
		Object.entries(findingsBySlot)
			.filter(([, list]) => (list ?? []).length > 0)
			.sort(([a], [b]) => a.localeCompare(b))
	);
	const total = $derived(sections.reduce((n, [, list]) => n + list.length, 0));
	const judged = $derived(
		sections.reduce((n, [, list]) => n + list.filter((f) => !!verdicts[f.id]?.verdict).length, 0)
	);

	function noteOf(id: string): string {
		return notes[id] ?? verdicts[id]?.note ?? '';
	}

	function setVerdict(id: string, verdict: string) {
		const entry: Entry = { verdict: verdict as FindingVerdictValue, note: noteOf(id) };
		onverdict(id, entry);
		saver.schedule(id, entry);
		void saver.flush();
	}

	function setNote(id: string, note: string) {
		notes[id] = note;
		const verdict = verdicts[id]?.verdict;
		if (!verdict) return;
		const entry: Entry = { verdict, note };
		onverdict(id, entry);
		saver.schedule(id, entry);
	}

	function location(f: ReviewFinding): string {
		if (!f.file_path) return '';
		return f.line ? `${f.file_path}:${f.line}` : f.file_path;
	}
</script>

{#if total === 0}
	<EmptyState compact title="No findings to review" description="The automated tools reported nothing that was sampled for this app." />
{:else}
	<div class="space-y-4">
		<p class="text-xs text-muted-foreground">
			<span class="font-mono tabular-nums">{judged}/{total}</span> reviewed. Each finding is what an automated tool reported,
			with the tool's own level. Decide whether the problem is real in this app.
		</p>
		{#each sections as [slot, list] (slot)}
			<section class="space-y-2" aria-label="Findings for {slotLabels[slot] ?? slot}">
				{#if sections.length > 1}<h4 class="section-label">{slotLabels[slot] ?? slot}</h4>{/if}
				<ol class="space-y-3">
					{#each list as f, i (f.id)}
						{@const entry = verdicts[f.id]}
						{@const docs = safeHttpUrl(f.docs_url)}
						{@const saveState = saver.states[f.id]}
						<li class="rounded-md border border-border bg-surface-1">
							<div class="flex flex-wrap items-center gap-1.5 border-b border-border px-3 py-2 text-xs">
								<span class="font-mono text-[11px] text-muted-foreground tabular-nums">#{i + 1}</span>
								<span class="font-medium">{f.tool_name || f.tool}</span>
								<LevelBadge label={f.tool_severity} fallback="unrated" />
								{#if f.category}<span class="text-muted-foreground">{f.category}</span>{/if}
								{#if f.rule_id}<span class="min-w-0 truncate font-mono text-[11px] text-muted-foreground" title={f.rule_id}>{f.rule_id}</span>{/if}
								{#if saveState === 'saving' || saveState === 'pending'}
									<LoaderCircle class="ml-auto size-3.5 animate-spin text-muted-foreground" aria-label="Saving" />
								{:else if saveState === 'error'}
									<button type="button" class="ml-auto cursor-pointer text-[11px] font-medium text-danger-fg underline" onclick={() => saver.retry(f.id)}>
										Not saved — retry
									</button>
								{/if}
							</div>
							<div class="space-y-2 px-3 py-2.5">
								{#if f.title}<p id="{uid}-{f.id}" class="break-words text-sm font-medium leading-snug">{f.title}</p>{/if}
								{#if f.file_path}
									<p class="flex min-w-0 items-center gap-1.5 font-mono text-xs">
										{#if onopensource && canOpenSource(slot as Slot)}
											<button
												type="button"
												class="inline-flex min-w-0 cursor-pointer items-center gap-1 text-primary hover:underline"
												title="Open in the source viewer"
												onclick={() => onopensource(slot as Slot, f.file_path, f.line)}
											>
												<FileCode class="size-3.5 shrink-0" aria-hidden="true" />
												<span class="truncate">{location(f)}</span>
											</button>
										{:else}
											<span class="truncate text-muted-foreground">{location(f)}</span>
										{/if}
									</p>
								{/if}
								{#if f.url || f.method || f.param}
									<p class="break-all font-mono text-xs text-muted-foreground">
										{#if f.method}<span class="font-medium text-foreground">{f.method}</span>{/if}
										{f.url}
										{#if f.param}<span> · param <span class="text-foreground">{f.param}</span></span>{/if}
									</p>
								{/if}
								{#if f.description}
									<div>
										<p class={expanded[f.id] ? 'whitespace-pre-line break-words text-xs text-muted-foreground' : 'line-clamp-3 whitespace-pre-line break-words text-xs text-muted-foreground'}>
											{f.description}
										</p>
										{#if f.description.length > 240}
											<button type="button" class="cursor-pointer text-[11px] text-primary hover:underline" onclick={() => (expanded[f.id] = !expanded[f.id])}>
												{expanded[f.id] ? 'Show less' : 'Show more'}
											</button>
										{/if}
									</div>
								{/if}
								{#if f.code_snippet}
									<pre class="max-h-48 overflow-auto rounded border border-border bg-muted/50 px-2 py-1.5 font-mono text-[11px] leading-relaxed"><code>{f.code_snippet}</code></pre>
								{/if}
								{#if f.evidence}
									<div>
										<p class="section-label">Evidence</p>
										<pre class="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-muted/50 px-2 py-1.5 font-mono text-[11px]">{f.evidence}</pre>
									</div>
								{/if}
								{#if docs || f.cwe}
									<p class="flex flex-wrap items-center gap-3 text-xs">
										{#if f.cwe}<span class="font-mono text-muted-foreground">{f.cwe}</span>{/if}
										{#if docs}
											<a href={docs} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary hover:underline">
												Rule documentation<ExternalLink class="size-3" aria-hidden="true" />
											</a>
										{/if}
									</p>
								{/if}
								<div class="space-y-1.5 pt-1">
									<ChoiceGroup
										value={entry?.verdict ?? ''}
										options={VERDICTS}
										size="sm"
										{readOnly}
										aria-label="Your verdict on finding {i + 1}"
										onchange={(v) => setVerdict(f.id, v)}
									/>
									{#if readOnly}
										{#if entry?.note}<p class="whitespace-pre-line break-words text-xs text-muted-foreground">Note: {entry.note}</p>{/if}
									{:else}
										<Textarea
											rows={2}
											class="min-h-0 text-xs"
											value={noteOf(f.id)}
											maxlength={2000}
											aria-label="Note on finding {i + 1}"
											placeholder={entry?.verdict ? 'Why? (optional)' : 'Pick a verdict first; then add a note if you like'}
											oninput={(e: Event) => setNote(f.id, (e.currentTarget as HTMLTextAreaElement).value)}
										/>
									{/if}
								</div>
							</div>
						</li>
					{/each}
				</ol>
			</section>
		{/each}
	</div>
{/if}
