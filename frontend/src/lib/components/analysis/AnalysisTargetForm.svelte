<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import Search from '@lucide/svelte/icons/search';
import FileCode from '@lucide/svelte/icons/file-code';
import ClipboardPaste from '@lucide/svelte/icons/clipboard-paste';
import LoadingState from '$lib/components/LoadingState.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import type { GenerationJobList } from '$lib/api/client';
import { tableFrame, shortModelName, modelNamePrefix } from '$lib/components/table';

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
				aria-pressed={sourceMode === 'job'}
				onclick={() => onSourceModeChange('job')}
			>
				<FileCode class="mr-1.5 h-3.5 w-3.5" />
				Generation Job
			</Button>
			<Button
				variant={sourceMode === 'paste' ? 'default' : 'outline'}
				size="sm"
				aria-pressed={sourceMode === 'paste'}
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
						aria-label="Search generation jobs"
						class="h-9 w-full rounded-md border border-input bg-background pl-9 pr-3 text-sm"
						bind:value={jobSearch}
					/>
				</div>
			</div>

			{#if jobsLoading}
				<LoadingState label="loading jobs" />
			{:else if jobsError}
				<ErrorAlert message={jobsError} onretry={onRetryLoadJobs} />
			{:else if filteredJobs.length === 0}
				<EmptyState
					compact
					icon={FileCode}
					title={jobs.length === 0 ? 'No completed generation jobs found' : 'No jobs match your search'}
					description={jobs.length === 0 ? 'Generate an app first, or paste code directly.' : undefined}
				>
					{#snippet action()}
						{#if jobs.length === 0}
							<Button variant="outline" size="sm" onclick={() => onSourceModeChange('paste')}>
								<ClipboardPaste class="mr-1.5 h-3.5 w-3.5" />
								Paste code instead
							</Button>
						{:else}
							<Button variant="outline" size="sm" onclick={() => (jobSearch = '')}>Clear search</Button>
						{/if}
					{/snippet}
				</EmptyState>
			{:else}
				<div class="table-frame rounded-md border border-border" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th>Job ID</th>
								<th>Model</th>
								<th>Template</th>
								<th>Completed</th>
								<th><span class="sr-only">Select</span></th>
							</tr>
						</thead>
						<tbody>
							{#each filteredJobs as job}
								{@const selected = selectedJobId === job.id}
								{@const provider = modelNamePrefix(job.model_name) || job.model_id_str?.split('/')[0] || ''}
								<tr data-selected={selected}>
									<td class="whitespace-nowrap font-mono text-xs">{job.id.slice(0, 8)}…</td>
									<td class="max-w-0 w-[35%]">
										{#if job.model_name}
											<div class="min-w-0" title={job.model_name}>
												<span class="block truncate">{shortModelName(job.model_name)}</span>
												{#if provider}<span class="cell-sub block truncate text-[11px] text-muted-foreground">{provider}</span>{/if}
											</div>
										{:else}
											<span class="text-muted-foreground">—</span>
										{/if}
									</td>
									<td>
										{#if job.template_name}{job.template_name}{:else}<span class="text-muted-foreground">—</span>{/if}
									</td>
									<td class="whitespace-nowrap text-xs text-muted-foreground">
										{job.completed_at ? new Date(job.completed_at).toLocaleDateString() : '—'}
									</td>
									<td class="w-px whitespace-nowrap text-right">
										<Button
											variant={selected ? 'default' : 'ghost'}
											size="sm"
											class="h-7 px-2 text-xs"
											aria-pressed={selected}
											onclick={() => onSelectJob(job.id)}
										>
											{selected ? 'Selected' : 'Select'}
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
					<label for="paste-backend" class="mb-1 block text-sm font-medium">Backend Code</label>
					<textarea
						id="paste-backend"
						class="h-40 w-full rounded-md border border-input bg-background p-3 font-mono text-sm"
						placeholder="Paste backend code here…"
						bind:value={pasteBackend}
					></textarea>
				</div>
				<div>
					<label for="paste-frontend" class="mb-1 block text-sm font-medium">Frontend Code</label>
					<textarea
						id="paste-frontend"
						class="h-40 w-full rounded-md border border-input bg-background p-3 font-mono text-sm"
						placeholder="Paste frontend code here…"
						bind:value={pasteFrontend}
					></textarea>
				</div>
			</div>
		{/if}
	</Card.Content>
</Card.Root>
