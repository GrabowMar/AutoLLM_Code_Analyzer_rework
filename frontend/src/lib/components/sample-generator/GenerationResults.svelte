<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import type { GenerationJob } from '$lib/api/client';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Code from '@lucide/svelte/icons/code';
	import Bot from '@lucide/svelte/icons/bot';
	import Square from '@lucide/svelte/icons/square';

	interface Props {
		mode: 'custom' | 'copilot';
		job: GenerationJob | null;
		statusColors: Record<string, string>;
		formatDuration: (s: number | null) => string;
		onCancel: (id: string) => void;
	}

	let { mode, job, statusColors, formatDuration, onCancel }: Props = $props();
</script>

<div class="space-y-4">
	{#if job}
		{#if mode === 'custom'}
			<Card.Root>
				<Card.Header class="pb-2">
					<div class="flex items-center justify-between">
						<Card.Title class="text-sm">Job Status</Card.Title>
						<Badge variant="outline" class="text-[10px] {statusColors[job.status] ?? ''}">
							{#if job.status === 'running' || job.status === 'pending'}
								<LoaderCircle class="mr-1 h-3 w-3 animate-spin" />
							{/if}
							{job.status}
						</Badge>
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
							<div class="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-400 mt-2">
								{job.error_message}
							</div>
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
						<pre class="max-h-[500px] overflow-x-auto overflow-y-auto rounded-b-xl bg-muted/50 p-4 text-xs font-mono leading-relaxed">{job.result_data.content}</pre>
					</Card.Content>
				</Card.Root>
			{/if}
		{:else}
			<Card.Root>
				<Card.Header class="pb-2">
					<div class="flex items-center justify-between">
						<Card.Title class="text-sm">Copilot Progress</Card.Title>
						<Badge variant="outline" class="text-[10px] {statusColors[job.status] ?? ''}">
							{#if job.status === 'running' || job.status === 'pending'}
								<LoaderCircle class="mr-1 h-3 w-3 animate-spin" />
							{/if}
							{job.status}
						</Badge>
					</div>
				</Card.Header>
				<Card.Content>
					<div class="space-y-3">
						<div class="text-sm">
							<div class="flex justify-between mb-1">
								<span class="text-muted-foreground">Iteration</span>
								<span class="font-mono">{job.copilot_current_iteration} / {job.copilot_max_iterations}</span>
							</div>
							<div class="h-1.5 rounded-full bg-muted overflow-hidden">
								<div
									class="h-full rounded-full bg-primary transition-all"
									style="width: {job.copilot_max_iterations > 0 ? (job.copilot_current_iteration / job.copilot_max_iterations) * 100 : 0}%"
								></div>
							</div>
						</div>
						{#if job.model_name}
							<div class="flex justify-between text-sm">
								<span class="text-muted-foreground">Model</span>
								<span>{job.model_name}</span>
							</div>
						{/if}
						{#if job.duration_seconds !== null}
							<div class="flex justify-between text-sm">
								<span class="text-muted-foreground">Duration</span>
								<span class="font-mono">{formatDuration(job.duration_seconds)}</span>
							</div>
						{/if}
						{#if job.app_directory}
							<div class="flex justify-between text-sm">
								<span class="text-muted-foreground">Directory</span>
								<span class="font-mono text-xs truncate max-w-[180px]">{job.app_directory}</span>
							</div>
						{/if}
						{#if job.error_message}
							<div class="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-400">
								{job.error_message}
							</div>
						{/if}
						{#if (job.status === 'running' || job.status === 'pending')}
							<Button variant="outline" size="sm" onclick={() => onCancel(job!.id)}>
								<Square class="mr-1.5 h-3.5 w-3.5" /> Cancel
							</Button>
						{/if}
					</div>
				</Card.Content>
			</Card.Root>

			{#if job.status === 'completed' && job.result_data && Object.keys(job.result_data).length > 0}
				<Card.Root>
					<Card.Header class="pb-2">
						<Card.Title class="text-sm">Result</Card.Title>
					</Card.Header>
					<Card.Content class="p-0">
						<pre class="max-h-[400px] overflow-auto rounded-b-xl bg-muted/50 p-4 text-xs font-mono leading-relaxed">{JSON.stringify(job.result_data, null, 2)}</pre>
					</Card.Content>
				</Card.Root>
			{/if}
		{/if}
	{:else}
		<Card.Root>
			<Card.Content class="py-12 text-center">
				{#if mode === 'custom'}
					<Code class="mx-auto h-10 w-10 text-muted-foreground/30" />
					<p class="mt-3 text-sm text-muted-foreground">Submit a prompt to see generation results here.</p>
				{:else}
					<Bot class="mx-auto h-10 w-10 text-muted-foreground/30" />
					<p class="mt-3 text-sm text-muted-foreground">Start the copilot to see progress here.</p>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
