<script lang="ts">
import { onMount, onDestroy } from 'svelte';
import * as Card from '$lib/components/ui/card';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import { containerTones } from '$lib/constants/colors';
import Server from '@lucide/svelte/icons/server';
import ExternalLink from '@lucide/svelte/icons/external-link';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import { Button } from '$lib/components/ui/button';
import { getContainers, inspectContainer, type ContainerInstance, type ContainerInspect } from '$lib/api/runtime';
import AppRuntimeControls from './AppRuntimeControls.svelte';

interface Props { jobId: string; jobStatus?: string }
let { jobId, jobStatus = '' }: Props = $props();

let container = $state<ContainerInstance | null>(null);
let inspect = $state<ContainerInspect | null>(null);
let loading = $state(true);
let poll: ReturnType<typeof setInterval> | null = null;

async function refresh() {
	try {
		const res = await getContainers({ job_id: jobId, per_page: 1 });
		container = res.containers[0] ?? null;
		if (container) {
			try { inspect = await inspectContainer(container.id); } catch { inspect = null; }
		} else {
			inspect = null;
		}
	} catch {
		// Keep the last known state; the next poll retries.
	} finally {
		loading = false;
	}
}

onMount(() => {
	refresh();
	poll = setInterval(refresh, 5000);
});
onDestroy(() => { if (poll) clearInterval(poll); });

// The runtime's own smoke check of the running container (not the conformance suite).
interface SmokeCheck { ok?: boolean; path?: string; method?: string; status?: number | null; probed?: string; admin?: boolean }
const smoke = $derived.by(() => {
	const raw = container?.metadata?.smoke;
	return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, any>) : null;
});
const smokeChecks = $derived((Array.isArray(smoke?.checks) ? smoke.checks : []) as SmokeCheck[]);
const healing = $derived.by(() => {
	const raw = container?.metadata?.healing;
	return raw && typeof raw === 'object' && !Array.isArray(raw) ? (raw as Record<string, any>) : null;
});
</script>

<section id="container" class="space-y-4">
	<Card.Root>
		<Card.Header class="flex flex-row flex-wrap items-start justify-between gap-3">
			<div class="space-y-1.5">
				<Card.Title>Runtime container</Card.Title>
				<Card.Description>Build, start and inspect the container serving this app.</Card.Description>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<AppRuntimeControls {jobId} {jobStatus} onChange={() => refresh()} />
				<Button variant="ghost" size="sm" onclick={refresh} title="Refresh" aria-label="Refresh container">
					<RefreshCw class="h-3.5 w-3.5" />
				</Button>
			</div>
		</Card.Header>
		<Card.Content class="space-y-4">

			{#if loading}
				<LoadingState label="loading container" class="py-8" />
			{:else if !container}
				<EmptyState compact icon={Server} title="No container yet" description="Use Build above to provision a runtime for this app." />
			{:else}
				<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
					<div>
						<span class="section-label">Status</span>
						<div><StatusBadge status={container.status} tones={containerTones} /></div>
					</div>
					<div>
						<span class="section-label">Updated</span>
						<div class="font-mono text-xs">{new Date(container.updated_at).toLocaleString()}</div>
					</div>
					<div>
						<span class="section-label">Container ID</span>
						<div class="font-mono text-xs break-all">{container.id}</div>
					</div>
					<div>
						<span class="section-label">Name</span>
						<div class="font-mono text-xs break-all">{container.name}</div>
					</div>
					<div class="md:col-span-2">
						<span class="section-label">Image</span>
						<div class="font-mono text-xs break-all">{inspect?.image || container.image || '—'}</div>
					</div>
				</div>

				<div>
					<h3 class="section-label mb-2">Ports</h3>
					<div class="overflow-x-auto">
						<table>
							<thead>
								<tr>
									<th>Service</th>
									<th class="num">Host port</th>
									<th class="num">Container port</th>
									<th><span class="sr-only">Actions</span></th>
								</tr>
							</thead>
							<tbody>
								{#if container.app_port}
									<tr>
										<td>App</td>
										<td class="num">{container.app_port}</td>
										<td class="num">8000</td>
										<td class="w-px whitespace-nowrap text-right">
											<a href={container.app_url ?? '#'} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-xs text-primary hover:underline">Open <ExternalLink class="h-3 w-3" /></a>
										</td>
									</tr>
								{:else}
									<tr><td colspan="4" class="text-center text-xs text-muted-foreground">No ports mapped</td></tr>
								{/if}
							</tbody>
						</table>
					</div>
				</div>

				{#if smoke}
					<div class="space-y-1.5">
						<div class="flex flex-wrap items-center gap-2">
							<h3 class="section-label">Runtime smoke check</h3>
							<StatusBadge status={smoke.passed ? 'passed' : 'failed'} />
							<span class="font-mono text-xs tabular-nums">{smoke.endpoints_ok ?? '—'}/{smoke.endpoints_checked ?? '—'} endpoints</span>
						</div>
						{#if smokeChecks.length}
							<div class="overflow-x-auto">
								<table class="text-xs">
									<thead><tr><th>Request</th><th class="num">Status</th><th>Result</th></tr></thead>
									<tbody>
										{#each smokeChecks as check, i (i)}
											<tr>
												<td class="font-mono">{check.method ?? 'GET'} {check.probed ?? check.path ?? ''}{check.admin ? ' (admin)' : ''}</td>
												<td class="num">{check.status ?? '—'}</td>
												<td><StatusBadge status={check.ok ? 'passed' : 'failed'} label={check.ok ? 'ok' : 'failed'} /></td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{/if}
						{#if healing}
							<p class="text-xs text-muted-foreground">
								Build healing <span class="font-mono">{healing.mode ?? '—'}</span>:
								{#if healing.changed}<span class="text-warning-fg">patched {Array.isArray(healing.patches) && healing.patches.length ? healing.patches.join(', ') : ''}</span>{:else}nothing patched{/if}
								{#if Array.isArray(healing.added_requirements) && healing.added_requirements.length} · added requirements <span class="font-mono">{healing.added_requirements.join(', ')}</span>{/if}
							</p>
						{/if}
						<p class="text-[11px] text-muted-foreground">The runtime's quick probe of this container, not the hidden conformance suite (see the Overview's evaluation).</p>
					</div>
				{/if}

				{#if container.last_error}
					<div class="rounded border border-destructive/30 bg-destructive/10 p-2 font-mono text-xs text-danger-fg">
						{container.last_error}
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
</section>
