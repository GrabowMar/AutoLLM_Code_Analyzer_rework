<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import {
		getCopilotIterations,
		type GenerationJob,
		type CopilotIteration,
	} from '$lib/api/client';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Code from '@lucide/svelte/icons/code';
	import Bot from '@lucide/svelte/icons/bot';
	import Square from '@lucide/svelte/icons/square';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import CircleX from '@lucide/svelte/icons/circle-x';

	interface Props {
		mode: 'custom' | 'copilot';
		job: GenerationJob | null;
		statusColors: Record<string, string>;
		formatDuration: (s: number | null) => string;
		onCancel: (id: string) => void;
	}

	let { mode, job, statusColors, formatDuration, onCancel }: Props = $props();

	let iterations = $state<CopilotIteration[]>([]);
	let iterationsLoading = $state(false);
	let expandedIteration = $state<number | null>(null);

	const copilotFiles = $derived.by((): [string, string][] => {
		const files = job?.result_data?.files;
		if (!files || typeof files !== 'object') return [];
		return Object.entries(files as Record<string, string>).sort(([a], [b]) => a.localeCompare(b));
	});

	const engineLabel = $derived(
		job?.result_data?.engine === 'aider' || job?.metrics?.engine === 'aider' ? 'Aider' : null,
	);

	let selectedCopilotFile = $state('');

	$effect(() => {
		if (copilotFiles.length > 0 && !selectedCopilotFile) {
			const preferred = copilotFiles.find(([p]) => p === 'backend/app.py' || p === 'app.py');
			selectedCopilotFile = preferred?.[0] ?? copilotFiles[0][0];
		}
	});

	async function loadIterations(jobId: string) {
		iterationsLoading = true;
		try {
			iterations = await getCopilotIterations(jobId);
		} catch {
			iterations = [];
		} finally {
			iterationsLoading = false;
		}
	}

	$effect(() => {
		if (mode !== 'copilot' || !job?.id) {
			iterations = [];
			return;
		}
		const st = job.status;
		if (st === 'running' || st === 'pending' || st === 'completed' || st === 'failed') {
			loadIterations(job.id);
		}
	});

	function truncate(text: string, max = 1200): string {
		if (text.length <= max) return text;
		return text.slice(0, max) + '\n… (truncated)';
	}
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
							<div
								class="rounded-md bg-red-500/10 border border-red-500/30 px-3 py-2 text-xs text-red-400 mt-2"
							>
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
						<Card.Title class="text-sm">Copilot Progress</Card.Title>
						<div class="flex items-center gap-1.5">
							{#if engineLabel}
								<Badge variant="secondary" class="text-[10px]">{engineLabel}</Badge>
							{/if}
							<Badge variant="outline" class="text-[10px] {statusColors[job.status] ?? ''}">
								{#if job.status === 'running' || job.status === 'pending'}
									<LoaderCircle class="mr-1 h-3 w-3 animate-spin" />
								{/if}
								{job.status}
							</Badge>
						</div>
					</div>
				</Card.Header>
				<Card.Content>
					<div class="space-y-3">
						<div class="text-sm">
							<div class="flex justify-between mb-1">
								<span class="text-muted-foreground">Iteration</span>
								<span class="font-mono"
									>{job.copilot_current_iteration} / {job.copilot_max_iterations}</span
								>
							</div>
							<div class="h-1.5 rounded-full bg-muted overflow-hidden">
								<div
									class="h-full rounded-full bg-primary transition-all"
									style="width: {job.copilot_max_iterations > 0 ? (job.copilot_current_iteration / job.copilot_max_iterations) * 100 : 0}%"
								></div>
							</div>
						</div>
						{#if job.scaffolding_name}
							<div class="flex justify-between text-sm">
								<span class="text-muted-foreground">Scaffold</span>
								<span class="text-xs">{job.scaffolding_name}</span>
							</div>
						{/if}
						{#if job.bundle_name || job.bundle_slug}
							<div class="flex justify-between text-sm">
								<span class="text-muted-foreground">Bundle</span>
								<span class="text-xs font-mono">{job.bundle_name ?? job.bundle_slug}</span>
							</div>
						{/if}
						{#if job.experiment_seed != null}
							<div class="flex justify-between text-sm">
								<span class="text-muted-foreground">Seed</span>
								<span class="font-mono text-xs">{job.experiment_seed}</span>
							</div>
						{/if}
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
						{#if job.status === 'running' || job.status === 'pending'}
							<Button variant="outline" size="sm" onclick={() => onCancel(job!.id)}>
								<Square class="mr-1.5 h-3.5 w-3.5" /> Cancel
							</Button>
						{/if}
						{#if job.status === 'completed' && job.app_directory}
							<a
								href="/applications?search={encodeURIComponent(job.id.slice(0, 8))}"
								class="inline-flex items-center gap-1 text-xs text-primary hover:underline"
							>
								<ExternalLink class="h-3 w-3" /> Browse applications
							</a>
						{/if}
					</div>
				</Card.Content>
			</Card.Root>

			{#if iterationsLoading}
				<div class="flex items-center gap-2 text-xs text-muted-foreground py-2">
					<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading iterations…
				</div>
			{:else if iterations.length > 0}
				<Card.Root>
					<Card.Header class="pb-2">
						<Card.Title class="text-sm">Aider iterations ({iterations.length})</Card.Title>
					</Card.Header>
					<Card.Content class="space-y-2 max-h-[280px] overflow-y-auto">
						{#each iterations as it}
							<div class="rounded-md border text-xs">
								<button
									type="button"
									class="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"
									onclick={() =>
										(expandedIteration =
											expandedIteration === it.iteration_number ? null : it.iteration_number)}
								>
									{#if expandedIteration === it.iteration_number}
										<ChevronDown class="h-3.5 w-3.5 shrink-0" />
									{:else}
										<ChevronRight class="h-3.5 w-3.5 shrink-0" />
									{/if}
									<span class="font-mono font-medium">#{it.iteration_number}</span>
									<Badge variant="outline" class="text-[9px]">{it.action}</Badge>
									<span class="text-muted-foreground">
										{it.errors_detected?.length ?? 0} errors
									</span>
									<Badge
										variant="outline"
										class="ml-auto text-[9px] {it.build_success
											? 'text-emerald-500'
											: 'text-red-400'}"
									>
										{it.build_success ? 'build ok' : 'build fail'}
									</Badge>
								</button>
								{#if expandedIteration === it.iteration_number}
									<div class="border-t px-3 py-2 space-y-2">
										{#if it.errors_detected?.length}
											<ul class="space-y-1">
												{#each it.errors_detected as err}
													<li class="flex items-start gap-1.5 text-red-400/90">
														<CircleX class="h-3 w-3 shrink-0 mt-0.5" />
														<span>{err}</span>
													</li>
												{/each}
											</ul>
										{/if}
										{#if it.build_output}
											<pre class="max-h-32 overflow-auto rounded bg-muted/50 p-2 font-mono text-[10px]"
												>{truncate(it.build_output)}</pre
											>
										{/if}
									</div>
								{/if}
							</div>
						{/each}
					</Card.Content>
				</Card.Root>
			{/if}

			{#if job.status === 'completed' && (job.result_data?.content || copilotFiles.length > 0)}
				<Card.Root>
					<Card.Header class="pb-2">
						<div class="flex items-center justify-between gap-2">
							<Card.Title class="text-sm">Result</Card.Title>
							{#if engineLabel}
								<Badge variant="secondary" class="text-[10px]">{engineLabel}</Badge>
							{/if}
						</div>
					</Card.Header>
					<Card.Content class="p-0">
						{#if copilotFiles.length > 1}
							<div class="flex flex-wrap gap-1 border-b px-3 py-2">
								{#each copilotFiles as [path]}
									<button
										type="button"
										class="rounded px-2 py-0.5 text-[10px] font-mono transition-colors {selectedCopilotFile === path ? 'bg-primary text-primary-foreground' : 'bg-muted/50 text-muted-foreground hover:text-foreground'}"
										onclick={() => (selectedCopilotFile = path)}
									>
										{path}
									</button>
								{/each}
							</div>
							<pre
								class="max-h-[400px] overflow-auto bg-muted/50 p-4 text-xs font-mono leading-relaxed"
								>{copilotFiles.find(([p]) => p === selectedCopilotFile)?.[1] ??
									job.result_data.content}</pre
							>
						{:else}
							<pre
								class="max-h-[400px] overflow-auto rounded-b-xl bg-muted/50 p-4 text-xs font-mono leading-relaxed"
								>{job.result_data?.content ?? ''}</pre
							>
						{/if}
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
					<p class="mt-3 text-sm text-muted-foreground">
						Start Aider Copilot to see progress and iterations here.
					</p>
				{/if}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
