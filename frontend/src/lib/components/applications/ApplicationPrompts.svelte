<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
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
			entries.push({ label: 'System Prompt', badge: 'SYS', badgeColor: 'bg-blue-500/20 text-blue-400', content: job.custom_system_prompt });
		if (job.custom_user_prompt)
			entries.push({ label: 'User Prompt', badge: 'USR', badgeColor: 'bg-emerald-500/20 text-emerald-400', content: job.custom_user_prompt });
		if (job.result_data?.content)
			entries.push({ label: 'Response', badge: 'RES', badgeColor: 'bg-purple-500/20 text-purple-400', content: job.result_data.content.substring(0, 2000) + (job.result_data.content.length > 2000 ? '\n...(truncated for preview)' : '') });
	}

	for (const art of artifacts) {
		const msgs = art.request_payload?.messages ?? [];
		const respModel = art.response_payload?.model ?? art.request_payload?.model ?? '—';
		for (const msg of msgs) {
			const roleLabel = msg.role === 'system' ? 'System' : msg.role === 'user' ? 'User' : 'Assistant';
			const roleBadge = msg.role === 'system' ? 'SYS' : msg.role === 'user' ? 'USR' : 'AST';
			const roleColor = msg.role === 'system' ? 'bg-blue-500/20 text-blue-400' : msg.role === 'user' ? 'bg-emerald-500/20 text-emerald-400' : 'bg-purple-500/20 text-purple-400';
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
				badgeColor: 'bg-purple-500/20 text-purple-400',
				content: respContent.substring(0, 3000) + (respContent.length > 3000 ? '\n...(truncated)' : ''),
				meta: { stage: art.stage, model: respModel, finish: choices[0]?.finish_reason ?? '—' },
			});
		}
	}

	if (entries.length === 0 && job.mode === 'copilot' && job.copilot_description) {
		entries.push({ label: 'Copilot Description', badge: 'DESC', badgeColor: 'bg-teal-500/20 text-teal-400', content: job.copilot_description });
	}

	return entries;
});
</script>

<section id="prompts" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Terminal class="h-5 w-5" /> Prompts</h2>

	<!-- Source Legend -->
	<div class="flex items-center gap-2 flex-wrap">
		<span class="text-xs text-muted-foreground font-medium">Sources:</span>
		<Badge variant="outline" class="text-[10px] bg-sky-500/10 text-sky-400 border-sky-500/30">📋 Template</Badge>
		<Badge variant="outline" class="text-[10px] bg-emerald-500/10 text-emerald-400 border-emerald-500/30">📝 Requirements</Badge>
		<Badge variant="outline" class="text-[10px] bg-orange-500/10 text-orange-400 border-orange-500/30">🏗️ Scaffolding</Badge>
		<Badge variant="outline" class="text-[10px] bg-purple-500/10 text-purple-400 border-purple-500/30">📊 Metadata</Badge>
	</div>

	{#if promptEntries.length === 0}
		<Card.Root>
			<Card.Content class="py-12 text-center">
				<Terminal class="mx-auto h-10 w-10 text-muted-foreground/30 mb-3" />
				<p class="text-sm text-muted-foreground">No prompts recorded for this job</p>
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root>
			<Card.Content class="p-0">
				<div class="flex flex-col sm:flex-row" style="min-height: 300px; height: auto; max-height: 600px;">
					<!-- Left: Prompt Tree -->
					<div class="w-full sm:w-2/5 border-b sm:border-b-0 sm:border-r overflow-y-auto bg-muted/20 max-h-48 sm:max-h-none">
						<div class="p-2 text-xs font-medium text-muted-foreground uppercase tracking-wider border-b px-3 py-2">
							Prompt Exchange ({promptEntries.length})
						</div>
						{#each promptEntries as entry, i}
							<button
								class="w-full text-left px-3 py-2.5 text-sm border-b border-border/50 transition-colors flex items-start gap-2 {selectedPromptIdx === i ? 'bg-primary/10 border-l-2 border-l-primary' : 'hover:bg-muted/50'}"
								onclick={() => (selectedPromptIdx = i)}
							>
								<span class="shrink-0 text-[10px] font-bold px-1.5 py-0.5 rounded {entry.badgeColor}">{entry.badge}</span>
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
