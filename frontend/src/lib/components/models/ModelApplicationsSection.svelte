<script lang="ts">
	// The generated apps (GenerationRuns) of one model: the caller's runs, newest first, with their
	// outcome and runtime controls. GET /api/generation/runs/?model=<model id>.
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import { TableToolbar, rowLink, tableFrame } from '$lib/components/table';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import { listSamples } from '$lib/api/v3/samples';
	import type { RunListOut } from '$lib/api/v3/types';
	import { formatApiError } from '$lib/api/core';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { outcomeLabels, type RunOutcome } from '$lib/constants/colors';
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

	let runs = $state<RunListOut[]>([]);

	const KIND_LABELS: Record<string, string> = {
		app_agentic: 'Agentic app',
		slot_single_shot: 'Slot variant',
		slot_agent: 'Slot variant (agent)',
		reference: 'Reference',
	};

	/** The run's outcome once it has one; before that its phase or status. */
	function runState(run: RunListOut): { status: string; label: string } {
		if (run.outcome) return { status: run.outcome, label: outcomeLabels[run.outcome as RunOutcome] ?? run.outcome };
		const status = run.phase || run.status;
		return { status, label: status.replace(/_/g, ' ') };
	}
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
			const res = await listSamples({ model: modelId, page_size: 100 });
			runs = res.items;
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
			<Button size="sm" href="/studies/new?mode=app" title="Generate apps with this model in an app study (pick {slug} as a model level)">
				<Sparkles class="h-3.5 w-3.5 mr-1.5" />New
			</Button>
		</Card.Header>
		<Card.Content class="p-0">
			{#if loading}
				<LoadingState label="loading applications" class="py-10" />
			{:else if error}
				<div class="p-4"><ErrorAlert message={error} onretry={load} /></div>
			{:else if runs.length === 0}
				<div class="p-4">
					<EmptyState compact icon={AppWindow} title="No applications yet" description="No applications have been generated with this model.">
						{#snippet action()}
							<Button size="sm" variant="outline" href="/studies/new?mode=app">
								<Sparkles class="h-3.5 w-3.5 mr-1.5" />Generate the first one
							</Button>
						{/snippet}
					</EmptyState>
				</div>
			{:else}
				<TableToolbar>
					{#snippet summary()}
						{runs.length === total ? `${total}` : `${runs.length} of ${total}`} application{total === 1 ? '' : 's'} · click a row to open
					{/snippet}
				</TableToolbar>
				<div class="table-frame" {@attach tableFrame}>
					<table>
						<thead>
							<tr>
								<th>Application</th>
								<th>Setup</th>
								<th>Status</th>
								<th class="num">Created</th>
								<th class="text-right"><span class="sr-only">Actions</span></th>
							</tr>
						</thead>
						<tbody>
							{#each runs as run (run.id)}
								{@const href = `/samples/${run.id}`}
								{@const state = runState(run)}
								{@const setup = [run.labels?.stack_profile, run.labels?.prose_variant, run.labels?.auth_mode].filter(Boolean).join(' · ')}
								<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
								<tr class="row-link" onclick={rowLink(href)}>
									<td class="whitespace-nowrap">
										<a {href} class="font-mono text-xs font-medium hover:text-primary" title="Open sample {run.id}">{run.id.substring(0, 8)}</a>
										<span class="cell-sub block text-[11px] text-muted-foreground">
											<span>{KIND_LABELS[run.kind] ?? run.kind}</span>
											{#if run.labels?.harness_preset}<span class="mx-1 opacity-50">·</span><span class="font-mono" title="Harness preset">{run.labels.harness_preset}</span>{/if}
										</span>
									</td>
									<td class="max-w-[240px] truncate text-muted-foreground" title={setup || undefined}>
										{setup || '—'}
									</td>
									<td class="whitespace-nowrap">
										<StatusBadge status={state.status} label={state.label} />
										{#if run.usable === false}<span class="cell-sub block text-[11px] text-muted-foreground" title="Not usable: attrition, never counted as a failure">not usable</span>{/if}
									</td>
									<td class="num text-xs text-muted-foreground">{fmtDate(run.created_at)}</td>
									<td class="w-px whitespace-nowrap">
										<div class="flex items-center justify-end gap-1">
											<!-- Keep the runtime controls on one line; their root wraps by default. -->
											<div class="[&>div]:flex-nowrap">
												<AppRuntimeControls jobId={run.id} jobStatus={run.status} compact={true} showPorts={true} />
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
