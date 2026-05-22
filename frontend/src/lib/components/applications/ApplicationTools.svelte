<script lang="ts">
import { onMount } from 'svelte';
import * as Card from '$lib/components/ui/card';
import { Button } from '$lib/components/ui/button';
import { Badge } from '$lib/components/ui/badge';
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
let activeTab = $state<'api' | 'cmds' | 'env'>('api');

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
	const port = container.app_port;
	if (!port) { toast.error('No port mapped'); return; }
	const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
	const url = `http://${host}:${port}${path.startsWith('/') ? '' : '/'}${path}`;
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

const TABS: Array<{ k: 'api' | 'cmds' | 'env'; label: string }> = [
	{ k: 'api', label: 'API Tester' },
	{ k: 'cmds', label: 'Quick Commands' },
	{ k: 'env', label: 'Environment' },
];
</script>

<section id="tools" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Wrench class="h-5 w-5" /> Tools</h2>
	<Card.Root>
		<Card.Content class="p-4">
			{#if loading}
				<p class="text-sm text-muted-foreground">Loading…</p>
			{:else if !container}
				<p class="text-sm text-muted-foreground italic">Build and start a container to use these tools.</p>
			{:else}
				<div class="flex gap-1 border-b mb-3">
					{#each TABS as t (t.k)}
						<button type="button" onclick={() => (activeTab = t.k)} class="px-3 py-1.5 text-sm border-b-2 -mb-px {activeTab === t.k ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}">{t.label}</button>
					{/each}
				</div>

				{#if activeTab === 'api'}
					<div class="space-y-3">
						<div class="flex flex-wrap items-center gap-2">
							<select bind:value={method} class="rounded border bg-background px-2 py-1 text-sm">
								<option>GET</option><option>POST</option><option>PUT</option><option>DELETE</option>
							</select>
							<span class="text-xs font-mono text-muted-foreground border rounded px-2 py-1 bg-muted/30">:{container.app_port ?? '—'}</span>
							<input bind:value={path} placeholder="/path" class="flex-1 min-w-[200px] rounded border bg-background px-2 py-1 text-sm font-mono" />
							<Button size="sm" onclick={sendRequest} disabled={sending || container.status !== 'running'}>
								<Play class="h-3.5 w-3.5 mr-1.5" /> Send
							</Button>
							<Button size="sm" variant="outline" onclick={clearReq}>Clear</Button>
						</div>
						{#if method !== 'GET'}
							<textarea bind:value={body} rows="4" placeholder={'{"key": "value"}'} class="w-full rounded border bg-background px-2 py-1 text-xs font-mono"></textarea>
						{/if}
						{#if apiResp}
							{#if apiResp.error}
								<div class="text-xs text-red-400 bg-red-500/5 border border-red-500/20 rounded p-2">{apiResp.error}</div>
							{:else}
								<div class="text-xs">
									<Badge variant="outline" class="mb-2 {apiResp.status && apiResp.status < 400 ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30'}">HTTP {apiResp.status}</Badge>
									<pre class="bg-zinc-950 text-zinc-100 p-3 rounded max-h-[400px] overflow-auto whitespace-pre-wrap break-words font-mono">{apiResp.text}</pre>
								</div>
							{/if}
						{/if}
					</div>
				{:else if activeTab === 'cmds'}
					<div class="space-y-3">
						<div class="flex flex-wrap gap-2">
							{#each QUICK as q (q.key)}
								<Button size="sm" variant="outline" onclick={() => runCmd(q.key)} disabled={!!runningCmd || container.status !== 'running'}>
									{runningCmd === q.key ? 'Running…' : q.label}
								</Button>
							{/each}
						</div>
						{#if cmdOutput}
							{#if cmdOutput.error}
								<div class="text-xs text-red-400 bg-red-500/5 border border-red-500/20 rounded p-2">{cmdOutput.error}</div>
							{:else}
								<div class="text-xs">
									<Badge variant="outline" class="mb-2 {cmdOutput.exit_code === 0 ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-amber-500/10 text-amber-400 border-amber-500/30'}">
										{cmdOutput.action} · exit {cmdOutput.exit_code}
									</Badge>
									<pre class="bg-zinc-950 text-zinc-100 p-3 rounded max-h-[400px] overflow-auto whitespace-pre-wrap break-words font-mono">{cmdOutput.output || '(no output)'}</pre>
								</div>
							{/if}
						{/if}
					</div>
				{:else}
					<div>
						{#if !inspect}
							<p class="text-sm text-muted-foreground">No inspect data available.</p>
						{:else if Object.keys(inspect.env || {}).length === 0}
							<p class="text-sm text-muted-foreground italic">No environment variables.</p>
						{:else}
							<div class="overflow-x-auto">
								<table class="w-full text-sm">
									<thead>
										<tr class="border-b bg-muted/30">
											<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Key</th>
											<th class="px-3 py-2 text-left text-xs font-medium text-muted-foreground">Value</th>
										</tr>
									</thead>
									<tbody class="divide-y">
										{#each Object.entries(inspect.env) as [k, v] (k)}
											<tr class="hover:bg-muted/30">
												<td class="px-3 py-2 font-mono text-xs">{k}</td>
												<td class="px-3 py-2 font-mono text-xs break-all">{v}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							</div>
						{/if}
					</div>
				{/if}
			{/if}
		</Card.Content>
	</Card.Root>
</section>
