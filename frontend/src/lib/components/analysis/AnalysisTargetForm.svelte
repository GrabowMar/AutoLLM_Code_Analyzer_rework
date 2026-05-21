<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import Search from '@lucide/svelte/icons/search';
import Loader from '@lucide/svelte/icons/loader-circle';
import FileCode from '@lucide/svelte/icons/file-code';
import ClipboardPaste from '@lucide/svelte/icons/clipboard-paste';
import AlertCircle from '@lucide/svelte/icons/alert-circle';
import type { GenerationJobList } from '$lib/api/client';

type SourceMode = 'job' | 'paste';

interface Props {
	sourceMode: SourceMode;
	jobsLoading: boolean;
	jobsError: string;
	jobs: GenerationJobList[];
	filteredJobs: GenerationJobList[];
	selectedJobId: string | null;
	jobSearch: string;
	pasteBackend: string;
	pasteFrontend: string;
	onSourceModeChange: (mode: SourceMode) => void;
	onSelectJob: (id: string) => void;
	onRetryLoadJobs: () => void;
}

let {
	sourceMode,
	jobsLoading,
	jobsError,
	jobs,
	filteredJobs,
	selectedJobId,
	jobSearch = $bindable(),
	pasteBackend = $bindable(),
	pasteFrontend = $bindable(),
	onSourceModeChange,
	onSelectJob,
	onRetryLoadJobs,
}: Props = $props();
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Select Source</Card.Title>
		<Card.Description>Choose a completed generation job or paste code directly.</Card.Description>
	</Card.Header>
	<Card.Content>
		<div class="mb-4 flex gap-2">
			<Button
				variant={sourceMode === 'job' ? 'default' : 'outline'}
				size="sm"
				onclick={() => onSourceModeChange('job')}
			>
				<FileCode class="mr-1.5 h-3.5 w-3.5" />
				Generation Job
			</Button>
			<Button
				variant={sourceMode === 'paste' ? 'default' : 'outline'}
				size="sm"
				onclick={() => onSourceModeChange('paste')}
			>
				<ClipboardPaste class="mr-1.5 h-3.5 w-3.5" />
				Paste Code
			</Button>
		</div>

		{#if sourceMode === 'job'}
			<div class="mb-4">
				<div class="relative max-w-sm">
					<Search class="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
					<input
						type="text"
						placeholder="Search jobs..."
						class="h-9 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm"
						bind:value={jobSearch}
					/>
				</div>
			</div>

			{#if jobsLoading}
				<div class="flex items-center justify-center py-12 text-muted-foreground">
					<Loader class="mr-2 h-4 w-4 animate-spin" />
					Loading jobs…
				</div>
			{:else if jobsError}
				<div class="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
					<AlertCircle class="h-4 w-4 shrink-0" />
					{jobsError}
					<Button variant="outline" size="sm" class="ml-auto" onclick={onRetryLoadJobs}>Retry</Button>
				</div>
			{:else if filteredJobs.length === 0}
				<div class="py-12 text-center text-sm text-muted-foreground">
					{jobs.length === 0 ? 'No completed generation jobs found.' : 'No jobs match your search.'}
				</div>
			{:else}
				<div class="overflow-x-auto table-scroll-wrapper">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b bg-muted/30">
								<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Job ID</th>
								<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Model</th>
								<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Template</th>
								<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground">Completed</th>
								<th class="px-4 py-2.5 text-left text-xs font-medium text-muted-foreground"></th>
							</tr>
						</thead>
						<tbody class="divide-y">
							{#each filteredJobs as job}
								<tr class="transition-colors hover:bg-muted/30 {selectedJobId === job.id ? 'bg-primary/5' : ''}">
									<td class="px-4 py-2.5 font-mono text-xs">{job.id.slice(0, 8)}…</td>
									<td class="px-4 py-2.5">{job.model_name ?? '—'}</td>
									<td class="px-4 py-2.5">{job.template_name ?? '—'}</td>
									<td class="px-4 py-2.5 text-xs text-muted-foreground">
										{job.completed_at ? new Date(job.completed_at).toLocaleDateString() : '—'}
									</td>
									<td class="px-4 py-2.5">
										<Button
											variant={selectedJobId === job.id ? 'default' : 'outline'}
											size="sm"
											class="h-7 text-xs"
											onclick={() => onSelectJob(job.id)}
										>
											{selectedJobId === job.id ? 'Selected' : 'Select'}
										</Button>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		{:else}
			<div class="space-y-4">
				<div>
					<label class="mb-1 block text-sm font-medium">Backend Code</label>
					<textarea
						class="h-40 w-full rounded-md border border-input bg-background p-3 font-mono text-sm"
						placeholder="Paste backend code here…"
						bind:value={pasteBackend}
					></textarea>
				</div>
				<div>
					<label class="mb-1 block text-sm font-medium">Frontend Code</label>
					<textarea
						class="h-40 w-full rounded-md border border-input bg-background p-3 font-mono text-sm"
						placeholder="Paste frontend code here…"
						bind:value={pasteFrontend}
					></textarea>
				</div>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
