<script lang="ts">
	/**
	 * SetupStep — name, hypothesis and notes, then the two decisions that shape everything after: the
	 * mode (agentic app or slot regeneration) and the kind (exploratory or confirmatory), as explained
	 * option cards. Switching the mode drops the chosen units (tasks and targets do not carry over), so
	 * it asks first when there are any.
	 *
	 * Props: `builder` (`BuilderState`), `locked`.
	 */
	import type { StudyKind, StudyMode } from '$lib/api/v3/types';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
	import type { BuilderState } from '../builder.svelte';
	import { setKind, switchMode } from '../design';
	import BuilderGroup from '../BuilderGroup.svelte';
	import OptionCards from '../OptionCards.svelte';
	import Blocks from '@lucide/svelte/icons/blocks';
	import Compass from '@lucide/svelte/icons/compass';
	import PuzzleIcon from '@lucide/svelte/icons/puzzle';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';

	interface Props {
		builder: BuilderState;
		locked: boolean;
	}

	let { builder: b, locked }: Props = $props();

	const design = $derived(b.design);
	const unitCount = $derived(design.units.tasks.length + design.units.slot_targets.length);

	let pendingMode = $state<StudyMode | null>(null);
	let confirmOpen = $state(false);

	function chooseMode(mode: StudyMode) {
		if (mode === design.mode) return;
		if (unitCount > 0) {
			pendingMode = mode;
			confirmOpen = true;
		} else b.setDesign(switchMode(design, mode));
	}

	const MODE_ITEMS = [
		{
			value: 'app',
			title: 'Agentic app',
			icon: Blocks,
			badge: 'Default',
			description: 'A coding agent builds a whole app per sample from a task brief, in a sandbox.',
			points: ['Units are graded tasks with hidden test suites', 'Stack, starter, auth and prompts can be varied'],
		},
		{
			value: 'slot',
			title: 'Slot regeneration',
			icon: PuzzleIcon,
			description: 'Regenerate one part of frozen parent apps many times, to measure a pattern’s prevalence.',
			points: ['Units are slot targets in parent apps', 'The parents fix stack, starter, auth and prose'],
		},
	];
	const KIND_ITEMS = [
		{
			value: 'exploratory',
			title: 'Exploratory',
			icon: Compass,
			badge: 'Default',
			description: 'Look for effects. Analyses are chosen afterwards and labelled exploratory.',
			points: ['Adaptive precision stops allowed', 'Design stays editable until launch'],
		},
		{
			value: 'confirmatory',
			title: 'Confirmatory',
			icon: ShieldCheck,
			description: 'Pre-registered: the analysis plan is locked and hashed at launch; deviations are reported.',
			points: ['Adds the analysis plan to Measurement', 'No adaptive stops'],
		},
	];
</script>

<BuilderGroup title="About" description="Shown in lists, on the experiment page and in the report.">
	<div class="grid gap-4 @2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
		<div class="space-y-1.5 @2xl:col-span-2">
			<Label for="study-name">Name</Label>
			<Input
				id="study-name"
				class="h-10 text-base"
				value={b.meta.name}
				placeholder="e.g. Security guidance vs none on CRUD tasks"
				autocomplete="off"
				aria-describedby="study-name-help"
				oninput={(e) => b.setMeta({ name: (e.currentTarget as HTMLInputElement).value })}
			/>
			<p id="study-name-help" class="text-[11px] text-muted-foreground">Left empty, it is saved as “Untitled experiment”.</p>
		</div>
		<div class="space-y-1.5">
			<Label for="study-hypothesis">Hypothesis <span class="font-normal text-muted-foreground">(optional)</span></Label>
			<Textarea
				id="study-hypothesis"
				rows={3}
				value={b.meta.hypothesis}
				placeholder="e.g. Security guidance lowers the probe exploit rate without lowering the pass rate."
				oninput={(e: Event) => b.setMeta({ hypothesis: (e.currentTarget as HTMLTextAreaElement).value })}
			/>
		</div>
		<div class="space-y-1.5">
			<Label for="study-description">Notes <span class="font-normal text-muted-foreground">(optional)</span></Label>
			<Textarea
				id="study-description"
				rows={3}
				value={b.meta.description}
				placeholder="Context, motivation, links."
				oninput={(e: Event) => b.setMeta({ description: (e.currentTarget as HTMLTextAreaElement).value })}
			/>
		</div>
	</div>
</BuilderGroup>

<BuilderGroup title="What is generated" description="Decides what a unit is and which factors exist.">
	<OptionCards aria-label="Mode" value={design.mode} items={MODE_ITEMS} disabled={locked} onValueChange={(v) => chooseMode(v as StudyMode)} />
</BuilderGroup>

<BuilderGroup title="Kind of experiment" description="Whether the analysis is fixed before the data exists.">
	<OptionCards
		aria-label="Kind"
		value={design.kind}
		items={KIND_ITEMS}
		disabled={locked}
		onValueChange={(v) => b.setDesign(setKind(design, v as StudyKind))}
	/>
</BuilderGroup>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Switch to {pendingMode === 'slot' ? 'slot regeneration' : 'agentic app'}?"
	description="The {unitCount} chosen {design.mode === 'app' ? 'task' : 'target'}{unitCount === 1 ? '' : 's'} will be cleared: units don’t carry over between modes. Models and the factors both modes share are kept."
	confirmLabel="Switch and clear"
	onConfirm={() => {
		if (pendingMode) b.setDesign(switchMode(design, pendingMode));
		confirmOpen = false;
	}}
/>
