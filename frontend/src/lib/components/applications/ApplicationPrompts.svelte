<script lang="ts">
import * as Card from '$lib/components/ui/card';
import EmptyState from '$lib/components/EmptyState.svelte';
import { Button } from '$lib/components/ui/button';
import Terminal from '@lucide/svelte/icons/terminal';
import Copy from '@lucide/svelte/icons/copy';
import type { GenerationJob, GenerationArtifact } from '$lib/api/client';
import { copyText, parsePromptSegments, segmentBorderColors } from './utils';

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

const promptEntries = $derived.by<PromptEntry[]>(() => {
	const entries: PromptEntry[] = [];

	if (job.mode === 'custom') {
		if (job.custom_system_prompt)
			entries.push({ label: 'System Prompt', badge: 'SYS', badgeColor: 'bg-info/15 text-info-fg', content: job.custom_system_prompt });
		if (job.custom_user_prompt)
			entries.push({ label: 'User Prompt', badge: 'USR', badgeColor: 'bg-success/15 text-success-fg', content: job.custom_user_prompt });
		if (job.result_data?.content)
			entries.push({ label: 'Response', badge: 'RES', badgeColor: 'bg-primary/15 text-primary', content: job.result_data.content.substring(0, 2000) + (job.result_data.content.length > 2000 ? '\n...(truncated for preview)' : '') });
	}

	if (job.mode === 'agent') {
		// Agent runs: the two frozen texts every model received; the full
		// conversation lives in the run record (llm_calls.jsonl.gz).
		const snap = (job.resolved_bundle ?? {}) as Record<string, any>;
		if (snap.harness?.system_prompt)
			entries.push({ label: `Harness system prompt (v${snap.harness.system_prompt_version})`, badge: 'SYS', badgeColor: 'bg-info/15 text-info-fg', content: snap.harness.system_prompt });
		if (snap.brief?.text)
			entries.push({ label: `Task brief (v${snap.brief.version})`, badge: 'USR', badgeColor: 'bg-success/15 text-success-fg', content: snap.brief.text });
		const final = job.result_data?.agent?.harness_report?.final_message;
		if (final)
			entries.push({ label: 'Agent final message', badge: 'RES', badgeColor: 'bg-primary/15 text-primary', content: final });
	}

	for (const art of artifacts) {
		const msgs = art.request_payload?.messages ?? [];
		const respModel = art.response_payload?.model ?? art.request_payload?.model ?? '—';
		for (const msg of msgs) {
			const roleLabel = msg.role === 'system' ? 'System' : msg.role === 'user' ? 'User' : 'Assistant';
			const roleBadge = msg.role === 'system' ? 'SYS' : msg.role === 'user' ? 'USR' : 'AST';
			const roleColor = msg.role === 'system' ? 'bg-info/15 text-info-fg' : msg.role === 'user' ? 'bg-success/15 text-success-fg' : 'bg-primary/15 text-primary';
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
				badgeColor: 'bg-primary/15 text-primary',
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
