<script lang="ts">
	/**
	 * RenderContextPicker — the levels of one app-mode cell for a prompt preview (`context_from` of
	 * `POST /prompts/render/`): task, prose variant, stack, starter level, style, auth mode, guidance and
	 * harness preset. Used by the prompt playground and PromptTemplateEditor's live preview.
	 *
	 * Props: `value` (AppContextFrom), `onchange`, `compact` (two columns, fewer labels).
	 */
	import * as Select from '$lib/components/ui/select';
	import { Segmented } from '$lib/components/ui/segmented';
	import type { AppContextFrom, ProseVariant } from '$lib/api/v3/types';
	import { PROSE_VARIANTS } from '$lib/api/v3/types';
	import { cn } from '$lib/utils';
	import LibraryRefPicker from './LibraryRefPicker.svelte';

	interface Props {
		value: AppContextFrom;
		onchange: (v: AppContextFrom) => void;
		compact?: boolean;
	}

	let { value, onchange, compact = false }: Props = $props();

	const AUTH = [
		{ value: 'contract_bearer', label: 'Contract: bearer token' },
		{ value: 'cookie_session', label: 'Contract: cookie session' },
		{ value: 'spec_defined', label: 'Spec-defined (task auth flow)' },
	];
	const LEVELS = [
		{ value: 'bare', label: 'Bare' },
		{ value: 'minimal', label: 'Minimal' },
		{ value: 'full', label: 'Full' },
	];

	const set = (patch: Partial<AppContextFrom>) => onchange({ ...value, ...patch });
	const one = (refs: string[]) => refs[0] ?? null;
</script>

<div class={cn('grid gap-3', compact ? 'sm:grid-cols-2' : 'sm:grid-cols-2 xl:grid-cols-4')}>
	<div class="space-y-1">
		<span class="section-label">Task</span>
		<LibraryRefPicker kind="task" value={value.task ? [value.task] : []} onchange={(r) => set({ task: one(r) ?? '' })} aria-label="Task" />
	</div>
	<div class="space-y-1">
		<span class="section-label">Prose variant</span>
		<Segmented
			aria-label="Prose variant"
			size="sm"
			value={value.variant ?? 'standard'}
			items={PROSE_VARIANTS.map((v) => ({ value: v, label: v }))}
			onValueChange={(v) => set({ variant: v as ProseVariant })}
		/>
	</div>
	<div class="space-y-1">
		<span class="section-label">Stack</span>
		<LibraryRefPicker kind="stack" value={value.stack ? [value.stack] : []} onchange={(r) => set({ stack: one(r) })} aria-label="Stack" />
	</div>
	<div class="space-y-1">
		<span class="section-label">Starter level</span>
		<Select.Simple
			value={value.starter_level ?? ''}
			items={LEVELS}
			clearLabel="No starter"
			placeholder="No starter"
			aria-label="Starter level"
			onValueChange={(v) => set({ starter_level: (v || null) as AppContextFrom['starter_level'] })}
		/>
	</div>
	<div class="space-y-1">
		<span class="section-label">Style</span>
		<LibraryRefPicker kind="style" value={value.style ? [value.style] : []} onchange={(r) => set({ style: one(r) })} aria-label="Style" />
	</div>
	<div class="space-y-1">
		<span class="section-label">Auth mode</span>
		<Select.Simple
			value={value.auth_mode ?? 'contract_bearer'}
			items={AUTH}
			aria-label="Auth mode"
			onValueChange={(v) => set({ auth_mode: v as AppContextFrom['auth_mode'] })}
		/>
	</div>
	<div class="space-y-1">
		<span class="section-label">Guidance</span>
		<LibraryRefPicker kind="guidance" value={value.guidance ? [value.guidance] : []} onchange={(r) => set({ guidance: one(r) })} aria-label="Guidance" />
	</div>
	<div class="space-y-1">
		<span class="section-label">Harness preset</span>
		<LibraryRefPicker
			kind="harness_preset"
			value={value.harness_preset ? [value.harness_preset] : []}
			onchange={(r) => set({ harness_preset: one(r) })}
			aria-label="Harness preset"
		/>
	</div>
</div>
