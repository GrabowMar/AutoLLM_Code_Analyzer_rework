<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import { getGenerationJobs, type GenerationJobList } from '$lib/api/generation';
	import { formatApiError } from '$lib/api/core';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { jobDisplayStatus, jobPhaseTones } from '$lib/components/generator/batches';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import AppRuntimeControls from '$lib/components/applications/AppRuntimeControls.svelte';

	interface Props {
		modelId: string;
		slug: string;
		onLoaded?: (total: number) => void;
	}

	let { modelId, slug, onLoaded }: Props = $props();

	let jobs = $state<GenerationJobList[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');

	function fmtDate(s: string | null): string {
		if (!s) return '—';
		const d = new Date(s);
		return d.toLocaleString(undefined, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
	}

	async function load() {
		loading = true;
		error = '';
		try {
			const res = await getGenerationJobs({ model_id: modelId, per_page: 100 });
			jobs = res.items;
			total = res.total;
			onLoaded?.(res.total);
		} catch (e) {
			error = formatApiError(e, 'Failed to load applications.');
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<section id="applications" class="space-y-4">
	<Card.Root class="gap-0 overflow-clip py-0">
		<Card.Header class="flex flex-row items-center justify-between gap-2 border-b border-border py-3">
			<div class="flex items-center gap-2">
				<AppWindow class="h-4 w-4 text-muted-foreground" />
				<Card.Title class="text-base">Applications</Card.Title>
				<Badge variant="outline" class="text-xs">{total}</Badge>
			</div>
			<Button size="sm" href="/sample-generator?mode=agent&model={encodeURIComponent(slug)}">
				<Sparkles class="h-3.5 w-3.5 mr-1.5" />New
			</Button>
		</Card.Header>
		<Card.Content class="p-0">
			{#if loading}
				<LoadingState label="loading applications" class="py-10" />
			{:else if error}
				<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
			{:else if jobs.length === 0}
				<div class="p-4">
					<EmptyState compact icon={AppWindow} title="No applications yet" description="No applications have been generated with this model.">
						{#snippet action()}
							<Button size="sm" variant="outline" href="/sample-generator?mode=agent&model={encodeURIComponent(slug)}">
								<Sparkles class="h-3.5 w-3.5 mr-1.5" />Generate the first one
							</Button>
						{/snippet}
					</EmptyState>
				</div>
			{:else}
				<TableToolbar>
					{#snippet summary()}
						{jobs.length === total ? `${total}` : `${jobs.length} of ${total}`} application{total === 1 ? '' : 's'} · click a row to open
					{/snippet}
				</TableToolbar>
				<div class="table-frame" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th>Application</th>
								<th>Template</th>
								<th>Status</th>
								<th class="num">Created</th>
								<th class="text-right"><span class="sr-only">Actions</span></th>
							</tr>
						</thead>
						<tbody>
							{#each jobs as job (job.id)}
								{@const href = `/applications/${job.id}`}
								<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
								<tr class="row-link" onclick={rowLink(href)}>
									<td class="whitespace-nowrap">
										<a {href} class="font-mono text-xs font-medium hover:text-primary" title="Open application {job.id}">{job.id.substring(0, 8)}</a>
										<span class="cell-sub block text-[11px] text-muted-foreground">
											<span class="capitalize">{job.mode}</span>
											{#if job.harness}<span class="mx-1 opacity-50">·</span><span class="font-mono" title="Harness">{job.harness}</span>{/if}
										</span>
									</td>
									<td class="max-w-[240px] truncate text-muted-foreground" title={job.template_name ?? job.scaffolding_name ?? undefined}>
										{job.template_name ?? job.scaffolding_name ?? '—'}
									</td>
									<td class="whitespace-nowrap">
										<StatusBadge status={jobDisplayStatus(job)} tones={jobPhaseTones} />
									</td>
									<td class="num text-xs text-muted-foreground">{fmtDate(job.created_at)}</td>
									<td class="w-px whitespace-nowrap">
										<div class="flex items-center justify-end gap-1">
											<!-- Keep the runtime controls on one line; their root wraps by default. -->
											<div class="[&>div]:flex-nowrap">
												<AppRuntimeControls jobId={job.id} jobStatus={job.status} compact={true} showPorts={true} />
											</div>
											<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground hover:text-foreground" {href} title="View application" aria-label="View application">
												<ChevronRight class="size-4" />
											</Button>
										</div>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
