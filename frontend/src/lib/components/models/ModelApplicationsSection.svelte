<script lang="ts">
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import Eye from '@lucide/svelte/icons/eye';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import { getGenerationJobs, type GenerationJobList } from '$lib/api/generation';
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

	const statusClass: Record<string, string> = {
		completed: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
		failed: 'bg-red-500/15 text-red-400 border-red-500/30',
		running: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
		pending: 'bg-zinc-500/15 text-zinc-400 border-zinc-500/30',
		cancelled: 'bg-zinc-500/15 text-zinc-500 border-zinc-500/30',
	};

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
			error = `Failed to load applications: ${(e as Error).message}`;
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<section id="applications" class="space-y-4">
	<Card.Root>
		<Card.Header class="flex flex-row items-center justify-between gap-2">
			<div class="flex items-center gap-2">
				<AppWindow class="h-4 w-4 text-muted-foreground" />
				<Card.Title class="text-base">Applications</Card.Title>
				<Badge variant="outline" class="text-xs">{total}</Badge>
			</div>
			<Button size="sm" href="/sample-generator?mode=custom&model={slug}">
				<Sparkles class="h-3.5 w-3.5 mr-1.5" />New
			</Button>
		</Card.Header>
		<Card.Content>
			{#if loading}
				<div class="flex items-center justify-center py-10 text-muted-foreground">
					<LoaderCircle class="h-5 w-5 animate-spin" />
				</div>
			{:else if error}
				<div class="rounded-lg border border-destructive bg-destructive/10 p-3 text-sm text-destructive">{error}</div>
			{:else if jobs.length === 0}
				<div class="py-8 text-center text-sm text-muted-foreground">
					No applications generated with this model yet.
					<div class="mt-2">
						<Button size="sm" variant="outline" href="/sample-generator?mode=custom&model={slug}">
							<Sparkles class="h-3.5 w-3.5 mr-1.5" />Generate the first one
						</Button>
					</div>
				</div>
			{:else}
				<div class="overflow-x-auto">
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b text-left text-xs text-muted-foreground">
								<th class="py-2 pr-3 font-medium">#</th>
								<th class="py-2 pr-3 font-medium">Mode</th>
								<th class="py-2 pr-3 font-medium">Template</th>
								<th class="py-2 pr-3 font-medium">Status</th>
								<th class="py-2 pr-3 font-medium">Created</th>
								<th class="py-2 pr-3 font-medium text-right">Actions</th>
							</tr>
						</thead>
						<tbody>
							{#each jobs as job (job.id)}
								<tr class="border-b last:border-0 hover:bg-muted/30">
									<td class="py-2 pr-3 font-mono text-xs">{job.id.substring(0, 8)}</td>
									<td class="py-2 pr-3"><Badge variant="outline" class="text-xs">{job.mode}</Badge></td>
									<td class="py-2 pr-3 text-xs text-muted-foreground truncate max-w-[200px]">
										{job.template_name ?? job.scaffolding_name ?? '—'}
									</td>
									<td class="py-2 pr-3">
										<Badge variant="outline" class="text-xs {statusClass[job.status] ?? ''}">{job.status}</Badge>
									</td>
									<td class="py-2 pr-3 text-xs text-muted-foreground whitespace-nowrap">{fmtDate(job.created_at)}</td>
									<td class="py-2 pr-3">
										<div class="flex items-center justify-end gap-2">
											<AppRuntimeControls jobId={job.id} jobStatus={job.status} compact={true} showPorts={true} />
											<Button variant="outline" size="sm" href="/applications/{job.id}" title="View application">
												<Eye class="h-3.5 w-3.5" />
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
