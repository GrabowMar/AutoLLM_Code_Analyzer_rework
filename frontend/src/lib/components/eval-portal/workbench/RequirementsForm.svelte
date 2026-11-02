<script lang="ts">
	/**
	 * RequirementsForm — the requirement checklist of one app, grouped by spec
	 * source: a verdict per item (met / partial / not met / blocked / can't
	 * test) and a note where the block's rule asks for one.
	 *
	 * Props: `block`, `answers` (RequirementsAnswers), `items` (the frozen
	 * RequirementItem list from the assignment context), `readOnly`, `onchange(next)`.
	 */
	import type {
		RequirementItem,
		RequirementVerdict,
		RequirementsAnswers,
		RequirementsBlock,
	} from '$lib/api/types/human-eval';
	import { Textarea } from '$lib/components/ui/textarea';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ChoiceGroup, { type ChoiceOption } from './ChoiceGroup.svelte';
	import { requirementNoteRequired } from './completeness';

	interface Props {
		block: RequirementsBlock;
		answers: RequirementsAnswers;
		items: RequirementItem[];
		readOnly?: boolean;
		onchange: (next: RequirementsAnswers) => void;
	}

	let { block, answers, items, readOnly = false, onchange }: Props = $props();
	const uid = $props.id();

	const VERDICTS: ChoiceOption[] = [
		{ value: 'met', label: 'Met', tone: 'success', title: 'Works as the requirement describes' },
		{ value: 'partial', label: 'Partly', tone: 'warning', title: 'Works only in part or with problems' },
		{ value: 'not_met', label: 'Not met', tone: 'danger', title: 'Missing or does not work' },
		{
			value: 'blocked',
			label: 'Blocked',
			tone: 'danger',
			title: 'A defect of the app (crash, broken login, …) prevents testing it; counts as not met'
		},
		{
			value: 'cannot_test',
			label: "Can't test",
			tone: 'neutral',
			title: 'You could not test it for another reason (not counted)'
		}
	];

	const SOURCE_TITLES: Record<string, string> = {
		backend: 'Backend',
		frontend: 'Frontend',
		admin: 'Admin',
		api: 'API endpoints',
		admin_api: 'Admin API endpoints',
		custom: 'Additional checks'
	};
	const SOURCE_ORDER = ['frontend', 'backend', 'admin', 'api', 'admin_api', 'custom'];

	const groups = $derived.by(() => {
		const map = new Map<string, RequirementItem[]>();
		for (const item of items) {
			const list = map.get(item.source) ?? [];
			list.push(item);
			map.set(item.source, list);
		}
		const rank = (source: string) => {
			const i = SOURCE_ORDER.indexOf(source);
			return i === -1 ? SOURCE_ORDER.length : i;
		};
		return [...map.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
	});

	const given = $derived(answers.items ?? {});
	const answered = $derived(items.filter((i) => given[i.id]?.verdict).length);

	function setVerdict(id: string, verdict: string) {
		const prev = given[id] ?? {};
		onchange({ ...answers, items: { ...given, [id]: { ...prev, verdict: verdict as RequirementVerdict } } });
	}

	function setNote(id: string, note: string) {
		const prev = given[id];
		if (!prev?.verdict) return;
		onchange({ ...answers, items: { ...given, [id]: { ...prev, note } } });
	}
</script>

{#if items.length === 0}
	<EmptyState compact title="No requirements to check" description="This app's specification lists none for this section." />
{:else}
	<div class="space-y-5">
		<p class="text-xs text-muted-foreground">
			<span class="font-mono tabular-nums">{answered}/{items.length}</span> judged.
			<b class="font-medium text-foreground">Blocked</b> = a defect of the app keeps you from testing it;
			<b class="font-medium text-foreground">Can't test</b> = you could not test it for another reason.
		</p>
		{#each groups as [source, list] (source)}
			<section class="space-y-2" aria-labelledby="{uid}-{source}">
				<h3 id="{uid}-{source}" class="section-label">
					{SOURCE_TITLES[source] ?? source}
					<span class="ml-1 font-mono tabular-nums">{list.length}</span>
				</h3>
				<ul class="divide-y divide-border rounded-md border border-border">
					{#each list as item (item.id)}
						{@const entry = given[item.id]}
						{@const noteRequired = requirementNoteRequired(block, entry?.verdict)}
						<li class="space-y-2 px-3 py-2.5">
							<p id="{uid}-{item.id}" class="whitespace-pre-line break-words text-sm leading-snug">{item.text}</p>
							<ChoiceGroup
								value={entry?.verdict ?? ''}
								options={VERDICTS}
								size="sm"
								{readOnly}
								aria-labelledby="{uid}-{item.id}"
								onchange={(v) => setVerdict(item.id, v)}
							/>
							{#if block.note !== 'off' && entry?.verdict}
								{#if readOnly}
									{#if entry.note}
										<p class="whitespace-pre-line break-words text-xs text-muted-foreground">Note: {entry.note}</p>
									{/if}
								{:else}
									<Textarea
										rows={2}
										class="min-h-0 text-xs"
										value={entry.note ?? ''}
										maxlength={2000}
										aria-label="Note on: {item.text}"
										aria-required={noteRequired}
										aria-invalid={noteRequired && !(entry.note ?? '').trim()}
										placeholder={noteRequired ? 'What did you see? (required)' : 'Note (optional)'}
										oninput={(e: Event) => setNote(item.id, (e.currentTarget as HTMLTextAreaElement).value)}
									/>
								{/if}
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/each}
	</div>
{/if}
