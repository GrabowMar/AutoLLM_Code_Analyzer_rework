<script lang="ts">
	/**
	 * PostRunCard — the post-run configuration (PostRunEditor, saved with
	 * PUT /post-run/) and the history of post-runs (trigger, status, analysis
	 * progress, report link), with "Run now".
	 *
	 * Props: `experimentId`, `mode`, `data` (`GET /post-run/`; live-patched by
	 * `post_run` events), `onchange()`, `readonly` (archived: settings are
	 * shown but cannot be saved), `canRun` (false until a run completed:
	 * there is nothing to analyse yet).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { putPostRun, runPostRun, type PostRunConfig, type PostRunSpec } from '$lib/api/experiments';
	import { formatApiError } from '$lib/api/core';
	import { formatDateTime } from '$lib/utils/formatters';
	import History from '@lucide/svelte/icons/history';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import Save from '@lucide/svelte/icons/save';
	import PostRunEditor from './PostRunEditor.svelte';
	import { stableKey } from '$lib/components/generator/composer/draft';

	interface Props {
		experimentId: string;
		mode: 'agent' | 'scaffolding';
		data: PostRunConfig;
		onchange: () => void;
		readonly?: boolean;
		canRun?: boolean;
	}

	let { experimentId, mode, data, onchange, readonly = false, canRun = true }: Props = $props();

	let value = $state<PostRunSpec>(untrack(() => structuredClone($state.snapshot(data.config))));
	let saved = $state(untrack(() => stableKey(data.config)));
	let saving = $state(false);
	let running = $state(false);

	// A server-side change (another tab) replaces the form only when it is unedited.
	$effect(() => {
		const key = stableKey(data.config);
		if (key !== saved && stableKey($state.snapshot(value)) === saved) {
			value = structuredClone($state.snapshot(data.config));
			saved = key;
		}
	});

	const dirty = $derived(stableKey($state.snapshot(value)) !== saved);
	const active = $derived(data.runs.some((r) => r.status === 'pending' || r.status === 'running'));
	const TRIGGERS: Record<string, string> = { completed: 'on completion', manual: 'manual', schedule: 'schedule' };

	async function save() {
		saving = true;
		try {
			const result = await putPostRun(experimentId, $state.snapshot(value));
			saved = stableKey(result.config);
			toast.success('Post-run settings saved');
			onchange();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not save the post-run settings.'));
		} finally {
			saving = false;
		}
	}

	async function runNow() {
		running = true;
		try {
			await runPostRun(experimentId);
			toast.success('Post-run started');
			onchange();
		} catch (e) {
			toast.error(formatApiError(e, 'Could not start the post-run.'));
		} finally {
			running = false;
		}
	}
</script>

<Card.Root>
	<Card.Header class="flex-row items-start justify-between gap-2">
		<div class="space-y-1">
			<Card.Title>Post-run</Card.Title>
			<Card.Description>Analysis and the report run once every cell is done (or when you start them).</Card.Description>
		</div>
		<div class="flex gap-2">
			<Button
				variant="outline"
				size="sm"
				disabled={running || active || !canRun}
				title={active ? 'A post-run is in progress' : !canRun ? 'No run has completed yet: nothing to analyse' : ''}
				onclick={runNow}
			>
				{#if running}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Play />{/if}Run now
			</Button>
			{#if !readonly}
				<Button size="sm" disabled={!dirty || saving} onclick={save}>
					{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Save />{/if}Save
				</Button>
			{/if}
		</div>
	</Card.Header>
	<Card.Content class="space-y-5">
		{#if readonly}
			<p class="text-xs text-muted-foreground">This experiment is archived: its post-run settings can no longer be changed.</p>
		{/if}
		<PostRunEditor bind:value {mode} disabled={readonly} idPrefix="post-run-card" />

		<div class="space-y-2 border-t border-border/60 pt-4">
			<p class="section-label">History</p>
			{#if data.runs.length === 0}
				<EmptyState compact icon={History} title="No post-runs yet" />
			{:else}
				<div class="overflow-x-auto rounded-md border border-border">
					<table class="table-card-mobile">
						<thead>
							<tr>
								<th scope="col">Started</th>
								<th scope="col">Trigger</th>
								<th scope="col">Status</th>
								<th scope="col" class="num">Analyses</th>
								<th scope="col">Report</th>
							</tr>
						</thead>
						<tbody>
							{#each data.runs as run (run.id)}
								{@const p = run.progress ?? {}}
								<tr>
									<td data-label="Started" class="whitespace-nowrap font-mono text-xs tabular-nums">{formatDateTime(run.created_at)}</td>
									<td data-label="Trigger">{TRIGGERS[run.trigger] ?? run.trigger}</td>
									<td data-label="Status">
										<StatusBadge status={run.status} />
										{#if run.error}<span class="cell-sub mt-0.5 block max-w-60 truncate text-[11px] text-danger-fg" title={run.error}>{run.error}</span>{/if}
									</td>
									<td data-label="Analyses" class="num text-xs max-md:text-left">
										{#if p.analysis_total}
											{p.analysis_done ?? 0}/{p.analysis_total}{#if p.analysis_failed}<span class="text-danger-fg"> · {p.analysis_failed} failed</span>{/if}
										{:else}<span class="text-muted-foreground">—</span>{/if}
									</td>
									<td data-label="Report">
										{#if run.report_id}
											<a href="/reports/{run.report_id}" class="font-medium text-success-fg hover:underline">Open report</a>
										{:else}<span class="text-muted-foreground">—</span>{/if}
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
