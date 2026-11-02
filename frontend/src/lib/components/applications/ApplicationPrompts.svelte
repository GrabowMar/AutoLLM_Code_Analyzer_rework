<script lang="ts">
import * as Card from '$lib/components/ui/card';
import EmptyState from '$lib/components/EmptyState.svelte';
import { Button } from '$lib/components/ui/button';
import Terminal from '@lucide/svelte/icons/terminal';
import Copy from '@lucide/svelte/icons/copy';
import type { GenerationJob, GenerationArtifact } from '$lib/api/client';
import { copyText, parsePromptSegments, segmentBorderColors } from './utils';
import { onMount } from 'svelte';
import { getTrajectoryStep, type ObservedInputs } from '$lib/api/trajectory';
import { formatApiError } from '$lib/api/core';
import { fmtInt } from './agent/format';

interface PromptEntry {
	label: string;
	badge: string;
	badgeColor: string;
	content: string;
	meta?: Record<string, string>;
}

interface Props {
	job: GenerationJob;
	artifacts: GenerationArtifact[];
}

let { job, artifacts }: Props = $props();

let selectedPromptIdx = $state(0);

// Agent runs: what actually reached the model on the first call (the gateway
// log), next to the frozen texts. Identity hashes come from the trajectory
// record; the text itself is fetched from the run record.
const agentSection = $derived(
	job.mode === 'agent' ? ((job.result_data?.agent ?? null) as Record<string, any> | null) : null,
);
const observed = $derived((agentSection?.trajectory?.observed_inputs ?? null) as ObservedInputs | null);
let observedPrompt = $state<string | null>(null);
let observedTools = $state<string[]>([]);
let observedState = $state<'idle' | 'loading' | 'ready' | 'error'>('idle');
let observedError = $state('');

onMount(async () => {
	if (!agentSection) return;
	observedState = 'loading';
	try {
		const step = await getTrajectoryStep(job.id, observed?.seq ?? 1);
		observedPrompt = step.request.system_prompt;
		observedTools = step.request.tools_offered ?? [];
		observedState = 'ready';
	} catch (e) {
		observedError = formatApiError(e);
		observedState = 'error';
	}
});

const toolsOffered = $derived(observed?.tools_offered?.length ? observed.tools_offered : observedTools);

const promptEntries = $derived.by<PromptEntry[]>(() => {
	const entries: PromptEntry[] = [];

	if (job.mode === 'custom') {
		if (job.custom_system_prompt)
			entries.push({ label: 'System Prompt', badge: 'SYS', badgeColor: 'bg-info/15 text-info-fg', content: job.custom_system_prompt });
		if (job.custom_user_prompt)
			entries.push({ label: 'User Prompt', badge: 'USR', badgeColor: 'bg-success/15 text-success-fg', content: job.custom_user_prompt });
		if (job.result_data?.content)
			entries.push({ label: 'Response', badge: 'RES', badgeColor: 'bg-primary/15 text-success-fg', content: job.result_data.content.substring(0, 2000) + (job.result_data.content.length > 2000 ? '\n...(truncated for preview)' : '') });
	}

	if (job.mode === 'agent') {
		// Agent runs: the two frozen texts every model received; the full
		// conversation lives in the run record (llm_calls.jsonl.gz).
		const snap = (job.resolved_bundle ?? {}) as Record<string, any>;
		if (snap.harness?.system_prompt)
			entries.push({ label: `Harness system prompt (frozen${snap.harness.version ? `, ${snap.harness.name ?? 'harness'} ${snap.harness.version}` : ''})`, badge: 'SYS', badgeColor: 'bg-info/15 text-info-fg', content: snap.harness.system_prompt });
		if (observedState !== 'idle' && (observedPrompt || observedState !== 'ready'))
			entries.push({
				label: 'Observed system prompt (first LLM call)',
				badge: 'SYS',
				badgeColor: 'bg-info/15 text-info-fg',
				content:
					observedState === 'loading'
						? 'Loading the system prompt from the run record…'
						: observedState === 'error'
							? `Could not load it from the run record: ${observedError}`
							: (observedPrompt ?? ''),
				meta: {
					stage: `call ${observed?.seq ?? 1}`,
					tokens: observed?.first_call_prompt_tokens != null ? `${fmtInt(observed.first_call_prompt_tokens)} prompt tokens` : '—',
					sha256: observed?.system_prompt_sha256?.slice(0, 16) ?? '—',
					tools: toolsOffered.length ? toolsOffered.join(', ') : 'none',
				},
			});
		if (snap.brief?.text)
			entries.push({ label: `Task brief (v${snap.brief.version})`, badge: 'USR', badgeColor: 'bg-success/15 text-success-fg', content: snap.brief.text });
		const final = job.result_data?.agent?.harness_report?.final_message;
		if (final)
			entries.push({ label: 'Agent final message', badge: 'RES', badgeColor: 'bg-primary/15 text-success-fg', content: final });
	}

	for (const art of artifacts) {
		const msgs = art.request_payload?.messages ?? [];
		const respModel = art.response_payload?.model ?? art.request_payload?.model ?? '—';
		for (const msg of msgs) {
			const roleLabel = msg.role === 'system' ? 'System' : msg.role === 'user' ? 'User' : 'Assistant';
			const roleBadge = msg.role === 'system' ? 'SYS' : msg.role === 'user' ? 'USR' : 'AST';
			const roleColor = msg.role === 'system' ? 'bg-info/15 text-info-fg' : msg.role === 'user' ? 'bg-success/15 text-success-fg' : 'bg-primary/15 text-success-fg';
			entries.push({
				label: `${art.stage} — ${roleLabel}`,
				badge: roleBadge,
				badgeColor: roleColor,
				content: msg.content ?? '',
				meta: { stage: art.stage, model: respModel, tokens: `${art.prompt_tokens}+${art.completion_tokens}` },
			});
		}
		const choices = art.response_payload?.choices ?? [];
		if (choices.length > 0) {
			const respContent = choices[0]?.message?.content ?? '';
			entries.push({
				label: `${art.stage} — Response`,
				badge: 'RES',
				badgeColor: 'bg-primary/15 text-success-fg',
				content: respContent.substring(0, 3000) + (respContent.length > 3000 ? '\n...(truncated)' : ''),
				meta: { stage: art.stage, model: respModel, finish: choices[0]?.finish_reason ?? '—' },
			});
		}
	}

	if (entries.length === 0 && job.mode === 'copilot' && job.copilot_description) {
		entries.push({ label: 'Copilot Description', badge: 'DESC', badgeColor: 'bg-muted text-muted-foreground', content: job.copilot_description });
	}

	return entries;
});

const legend = [
	{ label: 'Template', cls: 'bg-primary' },
	{ label: 'Requirements', cls: 'bg-success' },
	{ label: 'Scaffolding', cls: 'bg-warning' },
	{ label: 'Metadata', cls: 'bg-muted-foreground' },
];
</script>

<section id="prompts" class="space-y-4">
	{#if agentSection && (observed || toolsOffered.length)}
		<Card.Root>
			<Card.Header class="pb-2">
				<Card.Title class="text-sm">Observed inputs</Card.Title>
				<Card.Description class="text-xs">
					What reached the model on the first call, from the gateway log. The harness adds run-specific lines (model name,
					date) to its frozen prompt; the template hash ignores those, so equal template hashes mean identical treatment.
				</Card.Description>
			</Card.Header>
			<Card.Content>
				<dl class="grid gap-x-4 gap-y-1.5 text-xs sm:grid-cols-2">
					<div class="flex justify-between gap-2"><dt class="text-muted-foreground">System prompt sha256</dt><dd class="truncate font-mono" title={observed?.system_prompt_sha256 ?? ''}>{observed?.system_prompt_sha256?.slice(0, 16) ?? '—'}</dd></div>
					<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Template sha256</dt><dd class="truncate font-mono" title={observed?.system_prompt_template_sha256 ?? ''}>{observed?.system_prompt_template_sha256?.slice(0, 16) ?? '—'}</dd></div>
					<div class="flex justify-between gap-2"><dt class="text-muted-foreground">System prompt</dt><dd class="font-mono tabular-nums">{fmtInt(observed?.system_prompt_chars)} chars · {fmtInt(observed?.system_messages)} message(s)</dd></div>
					<div class="flex justify-between gap-2" title="Prompt tokens of the first call: harness prompt + tool specs + brief (the per-run overhead before any work)"><dt class="text-muted-foreground">First-call prompt tokens</dt><dd class="font-mono tabular-nums">{fmtInt(observed?.first_call_prompt_tokens)}</dd></div>
					<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Tool spec sha256</dt><dd class="truncate font-mono" title={observed?.tool_spec_sha256 ?? ''}>{observed?.tool_spec_sha256?.slice(0, 16) ?? '—'}{observed?.tool_spec_chars ? ` · ${fmtInt(observed.tool_spec_chars)} chars` : ''}</dd></div>
					<div class="flex justify-between gap-2"><dt class="text-muted-foreground">Tools offered</dt><dd class="truncate font-mono">{toolsOffered.length ? toolsOffered.join(', ') : 'none'}</dd></div>
				</dl>
				{#if !observed}
					<p class="mt-2 text-[11px] text-muted-foreground">Hashes appear once the run's trajectory record is stored (backfill_agent_trajectories).</p>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
	{#if promptEntries.length === 0}
		<Card.Root>
			<Card.Content>
				<EmptyState compact icon={Terminal} title="No prompts recorded" description="This job has no stored prompt exchange." />
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root>
			<Card.Header>
				<Card.Title>Prompt exchange</Card.Title>
				<Card.Description class="flex flex-wrap items-center gap-x-3 gap-y-1">
					Sections are marked by source:
					{#each legend as l (l.label)}
						<span class="inline-flex items-center gap-1.5 text-xs"><span class="h-3 w-0.5 rounded-full {l.cls}" aria-hidden="true"></span>{l.label}</span>
					{/each}
				</Card.Description>
			</Card.Header>
			<Card.Content class="border-t p-0">
				<div class="flex max-h-[600px] min-h-[300px] flex-col sm:flex-row">
					<!-- Left: Prompt Tree -->
					<div class="w-full sm:w-2/5 border-b sm:border-b-0 sm:border-r overflow-y-auto bg-muted/20 max-h-48 sm:max-h-none">
						<div class="section-label border-b px-3 py-2">
							Prompt Exchange ({promptEntries.length})
						</div>
						{#each promptEntries as entry, i}
							<button
								class="w-full text-left px-3 py-2.5 text-sm border-b border-border/50 transition-colors flex items-start gap-2 {selectedPromptIdx === i ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'}"
								onclick={() => (selectedPromptIdx = i)}
							>
								<span class="shrink-0 rounded px-1.5 py-0.5 font-mono text-[11px] font-bold {entry.badgeColor}">{entry.badge}</span>
								<div class="min-w-0">
									<div class="font-medium truncate">{entry.label}</div>
									{#if entry.meta}
										<div class="text-xs text-muted-foreground mt-0.5">{entry.meta.stage ?? ''} · {entry.meta.tokens ?? ''}</div>
									{/if}
								</div>
							</button>
						{/each}
					</div>
					<!-- Right: Content Preview with Color-Coded Sections -->
					<div class="w-full sm:w-3/5 flex flex-col min-h-[200px]">
						<div class="flex items-center justify-between px-4 py-2 border-b bg-muted/20">
							<span class="text-sm font-medium">{promptEntries[selectedPromptIdx]?.label ?? ''}</span>
							<Button variant="ghost" size="sm" class="h-7" onclick={() => copyText(promptEntries[selectedPromptIdx]?.content ?? '', 'Copied')}>
								<Copy class="h-3.5 w-3.5 mr-1" />Copy
							</Button>
						</div>
						{#if promptEntries[selectedPromptIdx]?.meta}
							<div class="flex flex-wrap gap-3 px-4 py-1.5 border-b bg-muted/10 text-xs text-muted-foreground">
								{#each Object.entries(promptEntries[selectedPromptIdx].meta ?? {}) as [k, v]}
									<span><strong>{k}:</strong> {v}</span>
								{/each}
							</div>
						{/if}
						<div class="flex-1 overflow-auto p-4">
							{#each parsePromptSegments(promptEntries[selectedPromptIdx]?.content ?? '') as segment}
								{#if segment.type !== 'default'}
									<div class="border-l-2 pl-3 mb-1.5 py-0.5 rounded-r {segmentBorderColors[segment.type] ?? ''}">
										<pre class="text-xs font-mono whitespace-pre-wrap break-all overflow-x-auto text-foreground/90">{segment.content}</pre>
									</div>
								{:else}
									<pre class="text-xs font-mono whitespace-pre-wrap break-all overflow-x-auto text-foreground/90 mb-1.5">{segment.content}</pre>
								{/if}
							{/each}
						</div>
					</div>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</section>
