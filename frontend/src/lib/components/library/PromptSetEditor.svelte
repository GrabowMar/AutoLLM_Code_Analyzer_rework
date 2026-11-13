<script lang="ts">
	/**
	 * PromptSetEditor — `PromptSetContent`: the mode, which template fills each role, the fragment
	 * templates (auth contract per auth mode, network sentence per policy, the optional budget-neutral
	 * sentence) and, in app mode, how the harness's own system prompt is treated. Required roles and
	 * fragments are marked (the library lint enforces them).
	 *
	 * Props: `value`, `onchange`, `readonly`.
	 */
	import { Segmented } from '$lib/components/ui/segmented';
	import type { FragmentKey, PromptRole, PromptSetContent } from '$lib/api/v3/types';
	import { FRAGMENT_KEYS } from '$lib/api/v3/types';
	import FormRow from './FormRow.svelte';
	import LibraryRefPicker from './LibraryRefPicker.svelte';

	interface Props {
		value: PromptSetContent;
		onchange: (v: PromptSetContent) => void;
		readonly?: boolean;
	}

	let { value, onchange, readonly = false }: Props = $props();

	const ROLES: Record<'app' | 'slot', { role: PromptRole; required: boolean; help: string }[]> = {
		app: [
			{ role: 'brief', required: true, help: 'The task brief the agent receives.' },
			{ role: 'system', required: false, help: 'Appended to or replacing the harness system prompt (see below).' },
			{ role: 'user', required: false, help: 'An extra first user message, where the harness takes one.' },
		],
		slot: [
			{ role: 'slot_single_shot_system', required: false, help: 'Single-shot executor: system message (both single-shot roles go together).' },
			{ role: 'slot_single_shot_user', required: false, help: 'Single-shot executor: user message with the hole and its context.' },
			{ role: 'slot_agent_brief', required: false, help: 'Restricted-agent executor: the brief.' },
		],
	};

	const FRAGMENT_HELP: Record<FragmentKey, string> = {
		'auth_contract.contract_bearer': 'Auth contract text for the contract_bearer auth mode.',
		'auth_contract.cookie_session': 'Auth contract text for cookie_session.',
		'auth_contract.spec_defined': 'Auth contract text for spec_defined (the task’s own auth flow).',
		'network.none': 'Network sentence when the sandbox has no egress.',
		'network.packages': 'Network sentence when package registries are reachable.',
		budget_neutral: 'Optional: “The run has a fixed resource budget; work efficiently.” (never numbers).',
	};

	const set = (patch: Partial<PromptSetContent>) => onchange({ ...value, ...patch });

	function setRole(role: PromptRole, ref: string | undefined) {
		const roles = { ...value.roles };
		if (ref) roles[role] = ref;
		else delete roles[role];
		set({ roles });
	}

	function setFragment(key: FragmentKey, ref: string | undefined) {
		const fragments = { ...value.fragments };
		if (ref) fragments[key] = ref;
		else delete fragments[key];
		set({ fragments });
	}

	const roleFilter = (role: PromptRole) => (i: { content: unknown }) => (i.content as { role?: string }).role === role;
	const fragmentFilter = (i: { content: unknown }) => (i.content as { role?: string }).role === 'fragment';
	const fragmentRequired = (key: FragmentKey) => value.mode === 'app' && key !== 'budget_neutral';
</script>

<div class="space-y-5">
	<FormRow label="Mode" inline help="App sets render agentic whole-app briefs; slot sets render part regenerations.">
		<Segmented aria-label="Mode" value={value.mode} disabled={readonly} items={[{ value: 'app', label: 'App' }, { value: 'slot', label: 'Slot' }]} onValueChange={(v) => set({ mode: v as 'app' | 'slot' })} />
	</FormRow>

	<section class="space-y-3" aria-labelledby="ps-roles">
		<h3 id="ps-roles" class="section-label">Roles</h3>
		{#each ROLES[value.mode] as r (r.role)}
			<FormRow label="{r.role.replace(/_/g, ' ')}{r.required ? ' *' : ''}" inline help={r.help}>
				<div class="flex items-start gap-2">
					<div class="min-w-0 flex-1">
						<LibraryRefPicker kind="prompt" value={value.roles[r.role] ? [value.roles[r.role]!] : []} filter={roleFilter(r.role)} disabled={readonly} onchange={(refs) => setRole(r.role, refs[0])} placeholder="Not used" />
					</div>
					{#if value.roles[r.role] && !r.required && !readonly}
						<button type="button" class="mt-2 cursor-pointer text-xs text-muted-foreground hover:text-foreground" onclick={() => setRole(r.role, undefined)}>Remove</button>
					{/if}
				</div>
			</FormRow>
		{/each}
	</section>

	{#if value.mode === 'app'}
		<FormRow label="Harness system prompt" inline help="Native: the harness keeps its own system prompt (captured per version). Append adds the system role after it; override replaces it.">
			<Segmented
				aria-label="System prompt mode"
				value={value.system_prompt_mode}
				disabled={readonly}
				items={[
					{ value: 'harness_native', label: 'Native' },
					{ value: 'append', label: 'Append' },
					{ value: 'override', label: 'Override' },
				]}
				onValueChange={(v) => set({ system_prompt_mode: v as PromptSetContent['system_prompt_mode'] })}
			/>
		</FormRow>
	{/if}

	<section class="space-y-3" aria-labelledby="ps-fragments">
		<h3 id="ps-fragments" class="section-label">Fragments</h3>
		<p class="text-xs text-muted-foreground">Closed-enum levels (auth mode, network policy) render from these pinned templates, so the prompt set’s hash commits to their text.</p>
		{#each FRAGMENT_KEYS as key (key)}
			<FormRow label="{key}{fragmentRequired(key) ? ' *' : ''}" inline help={FRAGMENT_HELP[key]}>
				<div class="flex items-start gap-2">
					<div class="min-w-0 flex-1">
						<LibraryRefPicker kind="prompt" value={value.fragments[key] ? [value.fragments[key]!] : []} filter={fragmentFilter} disabled={readonly} onchange={(refs) => setFragment(key, refs[0])} placeholder="Not set" />
					</div>
					{#if value.fragments[key] && !fragmentRequired(key) && !readonly}
						<button type="button" class="mt-2 cursor-pointer text-xs text-muted-foreground hover:text-foreground" onclick={() => setFragment(key, undefined)}>Remove</button>
					{/if}
				</div>
			</FormRow>
		{/each}
	</section>
</div>
