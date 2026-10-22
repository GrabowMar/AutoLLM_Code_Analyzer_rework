<script lang="ts">
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import * as Tabs from '$lib/components/ui/tabs';
import StatusBadge from '$lib/components/StatusBadge.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import EmptyState from '$lib/components/EmptyState.svelte';
import Wrench from '@lucide/svelte/icons/wrench';
import Play from '@lucide/svelte/icons/play';
import { toast } from 'svelte-sonner';
import {
	getContainers,
	inspectContainer,
	execContainer,
	type ContainerInstance,
	type ContainerInspect,
} from '$lib/api/runtime';

interface Props { jobId: string }
let { jobId }: Props = $props();

let container = $state<ContainerInstance | null>(null);
let inspect = $state<ContainerInspect | null>(null);
let loading = $state(true);
let activeTab = $state('api');

let method = $state<'GET' | 'POST' | 'PUT' | 'DELETE'>('GET');
let path = $state('/');
let body = $state('');
let apiResp = $state<{ status?: number; text?: string; error?: string } | null>(null);
let sending = $state(false);

const QUICK = [
	{ key: 'health', label: 'Health Check' },
	{ key: 'structure', label: 'Project Structure' },
	{ key: 'disk', label: 'Disk Usage' },
	{ key: 'environment', label: 'Environment' },
	{ key: 'processes', label: 'Processes' },
] as const;
let cmdOutput = $state<{ action?: string; exit_code?: number; output?: string; error?: string } | null>(null);
let runningCmd = $state<string | null>(null);

async function refresh() {
	try {
		const res = await getContainers({ job_id: jobId, per_page: 1 });
		container = res.containers[0] ?? null;
		if (container) {
			try { inspect = await inspectContainer(container.id); } catch { inspect = null; }
		}
	} finally {
		loading = false;
	}
}
onMount(refresh);

async function sendRequest() {
	if (!container) return;
	const baseUrl = container.app_url;
	if (!baseUrl) { toast.error('No port mapped'); return; }
	const url = `${baseUrl}${path.startsWith('/') ? '' : '/'}${path}`;
	sending = true;
	apiResp = null;
	try {
		const init: RequestInit = { method };
		if (method !== 'GET' && body.trim()) {
			init.headers = { 'Content-Type': 'application/json' };
			init.body = body;
		}
		const r = await fetch(url, init);
		const text = await r.text();
		apiResp = { status: r.status, text };
	} catch (e) {
		apiResp = { error: (e as Error).message + ' (CORS or network — generated app must allow cross-origin requests)' };
	} finally {
		sending = false;
	}
}
function clearReq() { apiResp = null; body = ''; }

async function runCmd(action: string) {
	if (!container) return;
	runningCmd = action;
	cmdOutput = null;
	try {
		const r = await execContainer(container.id, action);
		cmdOutput = { action: r.action, exit_code: r.exit_code, output: r.output, error: r.error };
	} catch (e) {
		cmdOutput = { error: (e as Error).message };
	} finally {
		runningCmd = null;
	}
}

const TABS = [
	{ k: 'api', label: 'API Tester' },
	{ k: 'cmds', label: 'Quick Commands' },
	{ k: 'env', label: 'Environment' },
];
</script>

<section id="tools" class="space-y-4">
	<Card.Root>
		<Card.Header>
			<Card.Title>Container tools</Card.Title>
			<Card.Description>Send requests to the running app, run diagnostics and inspect its environment.</Card.Description>
		</Card.Header>
		<Card.Content>
			{#if loading}
				<LoadingState label="loading container" class="py-8" />
			{:else if !container}
				<EmptyState compact icon={Wrench} title="No container" description="Build and start a container to use these tools." />
			{:else}
				<Tabs.Root bind:value={activeTab}>
					<Tabs.List>
						{#each TABS as t (t.k)}
							<Tabs.Trigger value={t.k}>{t.label}</Tabs.Trigger>
						{/each}
					</Tabs.List>

					<Tabs.Content value="api" class="space-y-3">
						<div class="flex flex-wrap items-center gap-2">
							<select bind:value={method} aria-label="HTTP method" class="rounded border bg-background px-2 py-1 text-sm">
								<option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option>
							</select>
							<span class="text-xs font-mono text-muted-foreground border rounded px-2 py-1 bg-muted/30">{container.app_url ?? '—'}</span>
							<input bind:value={path} placeholder="/path" aria-label="Request path" class="flex-1 min-w-[200px] rounded border bg-background px-2 py-1 text-sm font-mono" />
							<Button size="sm" variant="outline" onclick={clearReq}>Clear</Button>
							<Button size="sm" onclick={sendRequest} disabled={sending || container.status !== 'running'}>
								<Play class="h-3.5 w-3.5" /> Send
							</Button>
						</div>
						{#if method !== 'GET'}
							<textarea bind:value={body} rows="4" aria-label="Request body" placeholder={'{"key": "value"}'} class="w-full rounded border bg-background px-2 py-1 text-xs font-mono"></textarea>
						{/if}
						{#if apiResp}
							{#if apiResp.error}
								<div class="rounded border border-destructive/30 bg-destructive/10 p-2 text-xs text-danger-fg">{apiResp.error}</div>
							{:else}
								<div class="space-y-2 text-xs">
									<StatusBadge status={null} tone={apiResp.status && apiResp.status < 400 ? 'success' : 'danger'} label="HTTP {apiResp.status}" />
									<pre class="bg-muted/40 text-foreground/90 p-3 rounded max-h-[400px] overflow-auto whitespace-pre-wrap break-words font-mono">{apiResp.text}</pre>
								</div>
							{/if}
						{/if}
					</Tabs.Content>

					<Tabs.Content value="cmds" class="space-y-3">
						<div class="flex flex-wrap gap-2">
							{#each QUICK as q (q.key)}
								<Button size="sm" variant="outline" onclick={() => runCmd(q.key)} disabled={!!runningCmd || container.status !== 'running'}>
									{runningCmd === q.key ? 'Running…' : q.label}
								</Button>
							{/each}
						</div>
						{#if cmdOutput}
							{#if cmdOutput.error}
								<div class="rounded border border-destructive/30 bg-destructive/10 p-2 text-xs text-danger-fg">{cmdOutput.error}</div>
							{:else}
								<div class="space-y-2 text-xs">
									<StatusBadge status={null} tone={cmdOutput.exit_code === 0 ? 'success' : 'warning'} label="{cmdOutput.action} · exit {cmdOutput.exit_code}" class="normal-case" />
									<pre class="bg-muted/40 text-foreground/90 p-3 rounded max-h-[400px] overflow-auto whitespace-pre-wrap break-words font-mono">{cmdOutput.output || '(no output)'}</pre>
								</div>
							{/if}
						{/if}
					</Tabs.Content>

					<Tabs.Content value="env">
						{#if !inspect}
							<EmptyState compact title="No inspect data available" />
						{:else if Object.keys(inspect.env || {}).length === 0}
							<EmptyState compact title="No environment variables" />
						{:else}
							<div class="overflow-x-auto">
								<table class="w-full text-sm">
									<thead>
										<tr>
											<th>Key</th>
											<th>Value</th>
										</tr>
									</thead>
									<tbody>
										{#each Object.entries(inspect.env) as [k, v] (k)}
											<tr>
												<td class="font-mono text-xs">{k}</td>
												<td class="font-mono text-xs break-all">{v}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{/if}
					</Tabs.Content>
				</Tabs.Root>
			{/if}
		</Card.Content>
	</Card.Root>
</section>
