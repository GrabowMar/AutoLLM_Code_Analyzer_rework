<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { type GenerationJob } from '$lib/api/client';
	import Code from '@lucide/svelte/icons/code';
	import Bot from '@lucide/svelte/icons/bot';
	import Square from '@lucide/svelte/icons/square';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import AgentRunSummary from '$lib/components/applications/AgentRunSummary.svelte';

	interface Props {
		mode: 'custom' | 'agent';
		job: GenerationJob | null;
		formatDuration: (s: number | null) => string;
		onCancel: (id: string) => void;
	}

	let { mode, job, formatDuration, onCancel }: Props = $props();

	const agentFiles = $derived.by((): [string, string][] => {
		const files = job?.result_data?.files;
		if (!files || typeof files !== 'object') return [];
		return Object.entries(files as Record<string, string>).sort(([a], [b]) => a.localeCompare(b));
	});

	let selectedFile = $state('');

	$effect(() => {
		if (agentFiles.length > 0 && !agentFiles.some(([p]) => p === selectedFile)) {
			const preferred = agentFiles.find(([p]) => p === 'app.py');
			selectedFile = preferred?.[0] ?? agentFiles[0][0];
		}
	});
</script>

<div class="space-y-4">
	{#if job}
		{#if mode === 'custom'}
			<Card.Root>
				<Card.Header class="pb-2">
					<div class="flex items-center justify-between">
						<Card.Title class="text-sm">Job Status</Card.Title>
						<StatusBadge status={job.status} />
					</div>
				</Card.Header>
				<Card.Content>
					<div class="space-y-2 text-sm">
						<div class="flex justify-between">
							<span class="text-muted-foreground">Job ID</span>
							<span class="font-mono text-xs">{job.id.slice(0, 8)}…</span>
						</div>
						{#if job.model_name}
							<div class="flex justify-between">
								<span class="text-muted-foreground">Model</span>
								<span>{job.model_name}</span>
							</div>
						{/if}
						<div class="flex justify-between">
							<span class="text-muted-foreground">Temperature</span>
							<span class="font-mono">{job.temperature}</span>
						</div>
						{#if job.duration_seconds !== null}
							<div class="flex justify-between">
								<span class="text-muted-foreground">Duration</span>
								<span class="font-mono">{formatDuration(job.duration_seconds)}</span>
							</div>
						{/if}
						{#if job.error_message}
							<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg mt-2">
								{job.error_message}
							</p>
						{/if}
					</div>
				</Card.Content>
			</Card.Root>

			{#if job.status === 'completed' && job.result_data?.content}
				<Card.Root>
					<Card.Header class="pb-2">
						<Card.Title class="text-sm">Result</Card.Title>
					</Card.Header>
					<Card.Content class="p-0">
						<pre
							class="max-h-[500px] overflow-x-auto overflow-y-auto rounded-b-xl bg-muted/50 p-4 text-xs font-mono leading-relaxed"
							>{job.result_data.content}</pre
						>
					</Card.Content>
				</Card.Root>
			{/if}
		{:else}
			<Card.Root>
				<Card.Header class="pb-2">
					<div class="flex flex-wrap items-center justify-between gap-2">
						<Card.Title class="text-sm">Agent job</Card.Title>
						<StatusBadge status={job.status} />
					</div>
				</Card.Header>
				<Card.Content>
					<div class="space-y-2 text-sm">
						<div class="flex justify-between">
							<span class="text-muted-foreground">Job ID</span>
							<span class="font-mono text-xs">{job.id.slice(0, 8)}…</span>
						</div>
						{#if job.model_name}
							<div class="flex justify-between">
								<span class="text-muted-foreground">Model</span>
								<span>{job.model_name}</span>
							</div>
						{/if}
						{#if job.template_name}
							<div class="flex justify-between">
								<span class="text-muted-foreground">App spec</span>
								<span>{job.template_name}</span>
							</div>
						{/if}
						{#if job.stack_slug}
							<div class="flex justify-between">
								<span class="text-muted-foreground">Stack</span>
								<span class="font-mono text-xs">{job.stack_slug}</span>
							</div>
						{/if}
						{#if job.experiment_seed != null}
							<div class="flex justify-between">
								<span class="text-muted-foreground">Seed</span>
								<span class="font-mono text-xs">{job.experiment_seed}</span>
							</div>
						{/if}
						{#if job.duration_seconds !== null}
							<div class="flex justify-between">
								<span class="text-muted-foreground">Duration</span>
								<span class="font-mono">{formatDuration(job.duration_seconds)}</span>
							</div>
						{/if}
						{#if job.status === 'running' || job.status === 'pending'}
							<p class="text-[11px] text-muted-foreground">
								The agent works in a sandbox until it finishes or its budget runs out; results appear when the run ends.
							</p>
						{/if}
						{#if job.error_message}
							<p class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg">
								{job.error_message}
							</p>
						{/if}
						<div class="flex flex-wrap items-center gap-2 pt-1">
							{#if job.status === 'running' || job.status === 'pending'}
								<Button variant="outline" size="sm" onclick={() => onCancel(job!.id)}>
									<Square /> Cancel
								</Button>
							{/if}
							<a href="/applications/{job.id}" class="inline-flex items-center gap-1 text-xs text-primary hover:underline">
								<ExternalLink class="h-3 w-3" /> Open job page
							</a>
						</div>
					</div>
				</Card.Content>
			</Card.Root>

			{#if job.status === 'completed'}
				<AgentRunSummary {job} compact />
			{/if}

			{#if job.status === 'completed' && agentFiles.length > 0}
				<Card.Root>
					<Card.Header class="pb-2">
						<Card.Title class="text-sm">Files ({agentFiles.length})</Card.Title>
					</Card.Header>
					<Card.Content class="p-0">
						<div class="flex flex-wrap gap-1 border-b px-3 py-2">
							{#each agentFiles as [path]}
								<button
									type="button"
									aria-pressed={selectedFile === path}
									class="rounded px-2 py-0.5 text-[11px] font-mono transition-colors {selectedFile === path ? 'bg-primary text-primary-foreground' : 'bg-muted/50 text-muted-foreground hover:text-foreground'}"
									onclick={() => (selectedFile = path)}
								>
									{path}
								</button>
							{/each}
						</div>
						<pre class="max-h-[400px] overflow-auto rounded-b-xl bg-muted/50 p-4 text-xs font-mono leading-relaxed"
							>{agentFiles.find(([p]) => p === selectedFile)?.[1] ?? ''}</pre
						>
					</Card.Content>
				</Card.Root>
			{/if}
		{/if}
	{:else}
		<Card.Root>
			<Card.Content>
				{#if mode === 'custom'}
					<EmptyState compact icon={Code} title="No result yet" description="Submit a prompt to see generation results here." />
				{:else}
					<EmptyState compact icon={Bot} title="No agent job selected" description="Start an agent run, or open one from the history below." />
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
