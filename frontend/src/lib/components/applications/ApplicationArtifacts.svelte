<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Database from '@lucide/svelte/icons/database';
import EmptyState from '$lib/components/EmptyState.svelte';
import ChevronDown from '@lucide/svelte/icons/chevron-down';
import ChevronRight from '@lucide/svelte/icons/chevron-right';
import type { GenerationArtifact } from '$lib/api/client';
import { fmt, fmtCost, fmtDate } from './utils';

interface Props {
	artifacts: GenerationArtifact[];
}

let { artifacts }: Props = $props();

let expandedArtifacts = $state<Set<number>>(new Set());

function toggleArtifact(id: number) {
	const next = new Set(expandedArtifacts);
	if (next.has(id)) next.delete(id);
	else next.add(id);
	expandedArtifacts = next;
}
</script>

<section id="artifacts" class="space-y-4">
	{#if artifacts.length === 0}
		<Card.Root>
			<Card.Content>
				<EmptyState compact icon={Database} title="No artifacts recorded" description="LLM request/response payloads appear here once stored." />
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root>
			<Card.Header>
				<Card.Title>LLM artifacts</Card.Title>
				<Card.Description>Stored request/response payloads per stage. Click a row to expand.</Card.Description>
			</Card.Header>
			<Card.Content class="border-t p-0">
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr>
								<th class="w-8"><span class="sr-only">Expand</span></th>
								<th>Stage</th>
								<th>Model</th>
								<th>Prompt</th>
								<th>Completion</th>
								<th>Cost</th>
								<th>Created</th>
							</tr>
						</thead>
						<tbody>
							{#each artifacts as art}
								<tr class="cursor-pointer" onclick={() => toggleArtifact(art.id)}>
									<td>
										{#if expandedArtifacts.has(art.id)}
											<ChevronDown class="h-4 w-4 text-muted-foreground" />
										{:else}
											<ChevronRight class="h-4 w-4 text-muted-foreground" />
										{/if}
									</td>
									<td><Badge variant="outline" class="text-xs">{art.stage}</Badge></td>
									<td class="font-mono text-xs">{art.request_payload?.model ?? art.response_payload?.model ?? '—'}</td>
									<td>{fmt(art.prompt_tokens, 0)}</td>
									<td>{fmt(art.completion_tokens, 0)}</td>
									<td>{fmtCost(art.total_cost)}</td>
									<td class="text-muted-foreground">{fmtDate(art.created_at)}</td>
								</tr>
								{#if expandedArtifacts.has(art.id)}
									<tr>
										<td colspan="7" class="px-4 py-4 bg-muted/20">
											<!-- Token bar -->
											{#if (art.prompt_tokens ?? 0) + (art.completion_tokens ?? 0) > 0}
												{@const total = (art.prompt_tokens ?? 0) + (art.completion_tokens ?? 0)}
												<div class="mb-4">
													<div class="text-xs text-muted-foreground mb-1">Token Breakdown ({total} total)</div>
													<div class="h-3 rounded-full overflow-hidden bg-muted flex">
														<div class="bg-info h-full" style="width: {(art.prompt_tokens / total) * 100}%"></div>
														<div class="bg-success h-full" style="width: {(art.completion_tokens / total) * 100}%"></div>
													</div>
													<div class="flex justify-between text-[11px] text-muted-foreground mt-0.5">
														<span>Prompt: {fmt(art.prompt_tokens, 0)}</span>
														<span>Completion: {fmt(art.completion_tokens, 0)}</span>
													</div>
												</div>
											{/if}
											<!-- Request Messages -->
											<div class="space-y-2">
												<div class="section-label">Request Messages</div>
												{#each (art.request_payload?.messages ?? []) as msg}
													<div class="border rounded-md">
														<div class="px-3 py-1.5 border-b bg-muted/30 flex items-center gap-2">
															<Badge variant="outline" class="text-[11px] {msg.role === 'system' ? 'bg-info/15 text-info-fg' : msg.role === 'user' ? 'bg-success/15 text-success-fg' : 'bg-primary/15 text-success-fg'}">{msg.role}</Badge>
															<span class="text-xs text-muted-foreground">{(msg.content?.length ?? 0)} chars</span>
														</div>
														<pre class="p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto">{msg.content ?? ''}</pre>
													</div>
												{/each}
											</div>
											<!-- Response -->
											{#if art.response_payload?.choices?.length > 0}
												<div class="space-y-2 mt-3">
													<div class="section-label">Response</div>
													<div class="border rounded-md">
														<div class="px-3 py-1.5 border-b bg-muted/30 flex items-center gap-2">
															<Badge variant="outline" class="text-[11px] bg-primary/15 text-success-fg">assistant</Badge>
															<span class="text-xs text-muted-foreground">finish: {art.response_payload.choices[0]?.finish_reason ?? '—'}</span>
														</div>
														<pre class="p-3 text-xs font-mono whitespace-pre-wrap break-all max-h-48 overflow-auto">{art.response_payload.choices[0]?.message?.content ?? '(empty)'}</pre>
													</div>
												</div>
											{/if}
										</td>
									</tr>
								{/if}
							{/each}
						</tbody>
					</table>
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</section>
