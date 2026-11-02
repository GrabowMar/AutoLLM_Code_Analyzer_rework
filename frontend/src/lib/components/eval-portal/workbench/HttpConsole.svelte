<script lang="ts">
	/**
	 * HttpConsole — send raw HTTP requests to the app under test (through the
	 * platform, which logs every exchange for the study): method, path with
	 * endpoint suggestions from the app spec, header rows (or pasted raw
	 * headers), body; the response with status, time, headers and the body
	 * (pretty-printed JSON). History stays in this browser; the Authorization
	 * header is only remembered when the participant asks for it, and that
	 * choice is per assignment (never inherited by the next person on a shared
	 * computer). Sign out, Leave and withdraw clear all of it
	 * (clearConsoleStorage).
	 *
	 * Props: `assignmentId`, `apps`, `slot` (bindable: which app), `hosting`
	 * (per slot), `readOnly`, `onreadonly()` (the server refused: 409 read_only).
	 */
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import Copy from '@lucide/svelte/icons/copy';
	import History from '@lucide/svelte/icons/history';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';
	import Send from '@lucide/svelte/icons/send';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import X from '@lucide/svelte/icons/x';
	import { sendConsoleRequest } from '$lib/api/human-eval-portal';
	import { apiErrorStatus } from '$lib/api/core';
	import type { ConsoleRequest, ConsoleResponse, HostingInfo, PortalApp } from '$lib/api/types/human-eval';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import * as Select from '$lib/components/ui/select';
	import { toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import {
		CONSOLE_METHODS,
		bodyIssue,
		buildHeaders,
		consoleErrorMessage,
		endpointSuggestions,
		headerValue,
		historyEntry,
		parseHeaderBlock,
		parseHistory,
		pathIssue,
		prettyBody,
		pushHistory,
		statusTone,
		withDefaultContentType,
		type ConsoleMethod,
		type HeaderRow,
		type HistoryEntry,
	} from './console';
	import { consoleStorageKey, isReadOnlyError, readStorage, slotTitle, writeStorage, type Slot } from './workbench';

	interface Props {
		assignmentId: string;
		apps: PortalApp[];
		slot: Slot;
		hosting: Partial<Record<string, HostingInfo>>;
		readOnly?: boolean;
		onreadonly?: () => void;
	}

	let { assignmentId, apps, slot = $bindable(), hosting, readOnly = false, onreadonly }: Props = $props();
	const uid = $props.id();

	const historyKey = $derived(consoleStorageKey('history', assignmentId));
	const authKey = $derived(consoleStorageKey('auth', assignmentId));
	const rememberKey = $derived(consoleStorageKey('rememberAuth', assignmentId));

	let method = $state<ConsoleMethod>('GET');
	let path = $state('/');
	let rows = $state<HeaderRow[]>([{ name: 'Accept', value: 'application/json' }]);
	let body = $state('');
	let rawMode = $state(false);
	let rawHeaders = $state('');
	let rememberAuth = $state(false);
	let sending = $state(false);
	let response = $state.raw<ConsoleResponse | null>(null);
	let responseFor = $state.raw<{ method: string; path: string } | null>(null);
	let sendError = $state('');
	let history = $state.raw<HistoryEntry[]>([]);
	let showHistory = $state(false);
	let suggestOpen = $state(false);
	let showHeaders = $state(false);
	let localSeq = 0;

	const app = $derived(apps.find((a) => a.slot === slot) ?? apps[0]);
	const running = $derived(hosting[slot]?.state === 'running');
	const suggestions = $derived(endpointSuggestions(app?.spec, path === '/' ? '' : path, 12));
	const hasBody = $derived(!['GET', 'HEAD'].includes(method));
	const effectiveRows = $derived(rawMode ? parseHeaderBlock(rawHeaders) : rows);
	const built = $derived(buildHeaders(effectiveRows));
	const pathProblem = $derived(pathIssue(path));
	const bodyProblem = $derived(hasBody ? bodyIssue(body) : null);
	const problems = $derived([pathProblem, bodyProblem, ...built.errors].filter((p): p is string => !!p));
	const pretty = $derived(response ? prettyBody(response.body, response.headers) : null);
	const responseHeaders = $derived(Object.entries(response?.headers ?? {}));

	// Restore history and the remembered Authorization header for this assignment.
	$effect(() => {
		const hk = historyKey;
		const ak = authKey;
		const rk = rememberKey;
		untrack(() => {
			history = parseHistory(readStorage(hk));
			rememberAuth = readStorage(rk) === '1';
			const auth = rememberAuth ? readStorage(ak) : null;
			if (auth && !rows.some((r) => r.name.toLowerCase() === 'authorization')) {
				rows = [...rows, { name: 'Authorization', value: auth }];
			}
		});
	});

	function persistAuth() {
		const auth = headerValue(built.headers, 'authorization');
		writeStorage(authKey, rememberAuth && auth ? auth : null);
	}

	function setRemember(value: boolean) {
		rememberAuth = value;
		writeStorage(rememberKey, value ? '1' : null);
		persistAuth();
	}

	function toggleRaw() {
		if (rawMode) {
			rows = parseHeaderBlock(rawHeaders);
			if (rows.length === 0) rows = [{ name: '', value: '' }];
		} else {
			rawHeaders = rows
				.filter((r) => r.name.trim() || r.value.trim())
				.map((r) => `${r.name}: ${r.value}`)
				.join('\n');
		}
		rawMode = !rawMode;
	}

	function pickSuggestion(s: { method: ConsoleMethod; path: string }) {
		method = s.method;
		path = s.path;
		suggestOpen = false;
	}

	function loadHistory(entry: HistoryEntry) {
		method = entry.method;
		path = entry.path;
		body = entry.body;
		const keepAuth = rows.find((r) => r.name.toLowerCase() === 'authorization');
		const loaded = Object.entries(entry.headers).map(([name, value]) => ({ name, value }));
		if (keepAuth && !loaded.some((r) => r.name.toLowerCase() === 'authorization')) loaded.push(keepAuth);
		rows = loaded.length ? loaded : [{ name: '', value: '' }];
		rawMode = false;
		if (apps.some((a) => a.slot === entry.slot)) slot = entry.slot;
		showHistory = false;
	}

	function clearHistory() {
		history = [];
		writeStorage(historyKey, null);
	}

	async function send(event?: SubmitEvent) {
		event?.preventDefault();
		if (sending || readOnly || problems.length > 0) return;
		const request: ConsoleRequest = {
			slot,
			method,
			path,
			headers: withDefaultContentType(built.headers, hasBody ? body : ''),
			body: hasBody ? body : '',
		};
		sending = true;
		sendError = '';
		persistAuth();
		try {
			const result = await sendConsoleRequest(assignmentId, request);
			response = result;
			responseFor = { method: request.method, path: request.path };
			history = pushHistory(history, historyEntry(request, result, rememberAuth));
		} catch (e) {
			if (isReadOnlyError(e)) {
				onreadonly?.();
				sendError = 'This assignment no longer accepts requests.';
				return;
			}
			const status = apiErrorStatus(e);
			sendError = consoleErrorMessage(status, e as { detail?: unknown; code?: unknown });
			if (status === null) {
				history = pushHistory(
					history,
					historyEntry(
						request,
						{ id: `local-${Date.now()}-${++localSeq}`, status_code: null, elapsed_ms: null, error: sendError },
						rememberAuth
					)
				);
			}
		} finally {
			sending = false;
			writeStorage(historyKey, JSON.stringify(history));
		}
	}

	async function copyBody() {
		if (!pretty) return;
		try {
			await navigator.clipboard.writeText(pretty.text);
			toast.success('Response body copied');
		} catch {
			toast.error('Could not copy to the clipboard');
		}
	}

	function statusClass(code: number | null): string {
		return toneClasses[statusTone(code)];
	}
</script>

<form class="flex h-full min-h-0 flex-col gap-2" onsubmit={send} aria-label="HTTP console">
	<div class="flex flex-wrap items-center gap-2">
		{#if apps.length > 1}
			<Segmented
				size="sm"
				aria-label="Send requests to"
				value={slot}
				onValueChange={(v) => (slot = v as Slot)}
				items={apps.map((a) => ({ value: a.slot, label: slotTitle(a) }))}
			/>
		{/if}
		{#if !running}
			<span class="text-xs text-warning-fg">Start {app?.label ?? 'the app'} to send requests.</span>
		{/if}
		<div class="ml-auto flex items-center gap-1">
			<Button
				type="button"
				variant="ghost"
				size="sm"
				class="h-7 text-xs"
				aria-expanded={showHistory}
				onclick={() => (showHistory = !showHistory)}
			>
				<History class="size-3.5" />
				History <span class="font-mono tabular-nums">{history.length}</span>
			</Button>
		</div>
	</div>

	{#if showHistory}
		<div class="max-h-52 overflow-y-auto rounded-md border border-border bg-card">
			{#if history.length === 0}
				<p class="px-3 py-2 text-xs text-muted-foreground">No requests yet. History is kept only in this browser.</p>
			{:else}
				<ul>
					{#each history as entry (entry.id)}
						<li class="border-b border-border last:border-b-0">
							<button
								type="button"
								class="flex w-full cursor-pointer items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-accent"
								onclick={() => loadHistory(entry)}
							>
								<span class="w-14 shrink-0 font-mono font-medium">{entry.method}</span>
								<span class="min-w-0 flex-1 truncate font-mono">{entry.path}</span>
								{#if apps.length > 1}<span class="shrink-0 font-mono text-muted-foreground">{entry.slot}</span>{/if}
								<span class={cn('shrink-0 rounded border px-1 font-mono text-[11px]', statusClass(entry.status_code))}>
									{entry.status_code ?? 'error'}
								</span>
							</button>
						</li>
					{/each}
				</ul>
				<div class="border-t border-border px-3 py-1.5 text-right">
					<button type="button" class="cursor-pointer text-[11px] text-muted-foreground hover:text-foreground hover:underline" onclick={clearHistory}>
						Clear history
					</button>
				</div>
			{/if}
		</div>
	{/if}

	<div class="flex gap-2">
		<div class="w-28 shrink-0">
			<Select.Simple
				size="sm"
				value={method}
				onValueChange={(v) => (method = v as ConsoleMethod)}
				aria-label="Method"
				items={CONSOLE_METHODS.map((m) => ({ value: m, label: m }))}
			/>
		</div>
		<div class="relative min-w-0 flex-1">
			<Input
				id="{uid}-path"
				bind:value={path}
				class="h-8 font-mono text-xs"
				placeholder="/api/items?page=1"
				aria-label="Path"
				aria-invalid={!!pathProblem}
				aria-autocomplete="list"
				aria-expanded={suggestOpen && suggestions.length > 0}
				aria-controls="{uid}-suggest"
				autocomplete="off"
				spellcheck="false"
				onfocus={() => (suggestOpen = true)}
				onblur={() => setTimeout(() => (suggestOpen = false), 150)}
				onkeydown={(e: KeyboardEvent) => {
					if (e.key === 'Escape') suggestOpen = false;
				}}
			/>
			{#if suggestOpen && suggestions.length > 0}
				<ul
					id="{uid}-suggest"
					class="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-y-auto rounded-md border border-border bg-popover shadow-[var(--shadow-pop)]"
					aria-label="Endpoints from the specification"
				>
					<li class="px-3 pt-1.5 text-[11px] text-muted-foreground">From the app's specification</li>
					{#each suggestions as s (`${s.method} ${s.path}`)}
						<li>
							<button
								type="button"
								class="flex w-full cursor-pointer items-baseline gap-2 px-3 py-1.5 text-left text-xs hover:bg-accent"
								onmousedown={(e) => e.preventDefault()}
								onclick={() => pickSuggestion(s)}
							>
								<span class="w-14 shrink-0 font-mono font-medium">{s.method}</span>
								<span class="min-w-0 break-all font-mono">{s.path}</span>
								{#if s.description}<span class="ml-auto hidden min-w-0 truncate text-muted-foreground sm:block">{s.description}</span>{/if}
							</button>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
		<Button type="submit" size="sm" disabled={sending || readOnly || problems.length > 0 || !running} aria-busy={sending}>
			{#if sending}<LoaderCircle class="animate-spin" />{:else}<Send />{/if}
			Send
		</Button>
	</div>

	<div class="min-h-0 space-y-2 overflow-y-auto">
		<details class="rounded-md border border-border" bind:open={showHeaders}>
			<summary class="cursor-pointer px-3 py-1.5 text-xs font-medium">
				Headers <span class="font-mono text-muted-foreground tabular-nums">{Object.keys(built.headers).length}</span>
			</summary>
			<div class="space-y-2 border-t border-border px-3 py-2">
				{#if rawMode}
					<Textarea
						rows={4}
						class="min-h-0 font-mono text-xs"
						bind:value={rawHeaders}
						placeholder={'Authorization: Bearer …\nX-Requested-With: XMLHttpRequest'}
						aria-label="Headers, one per line (Name: value)"
						spellcheck="false"
					/>
				{:else}
					<ul class="space-y-1.5">
						{#each rows as row, i (i)}
							<li class="flex gap-1.5">
								<Input bind:value={row.name} class="h-7 w-2/5 font-mono text-xs" placeholder="Name" aria-label="Header {i + 1} name" spellcheck="false" />
								<Input bind:value={row.value} class="h-7 min-w-0 flex-1 font-mono text-xs" placeholder="Value" aria-label="Header {i + 1} value" spellcheck="false" />
								<Button type="button" variant="ghost" size="icon" class="size-7" aria-label="Remove header {i + 1}" onclick={() => (rows = rows.filter((_, j) => j !== i))}>
									<X class="size-3.5" />
								</Button>
							</li>
						{/each}
					</ul>
				{/if}
				<div class="flex flex-wrap items-center gap-3 text-xs">
					{#if !rawMode}
						<Button type="button" variant="outline" size="sm" class="h-7 text-xs" onclick={() => (rows = [...rows, { name: '', value: '' }])}>
							<Plus class="size-3.5" />
							Add header
						</Button>
					{/if}
					<button type="button" class="cursor-pointer text-primary hover:underline" onclick={toggleRaw}>
						{rawMode ? 'Edit as rows' : 'Paste raw headers'}
					</button>
					<label class="ml-auto flex items-center gap-1.5 text-muted-foreground">
						<Checkbox checked={rememberAuth} onCheckedChange={(v) => setRemember(v === true)} />
						Remember the Authorization header
					</label>
				</div>
				{#if built.dropped.length > 0}
					<p class="text-[11px] text-muted-foreground">Not sent (set by the platform): {built.dropped.join(', ')}</p>
				{/if}
			</div>
		</details>

		{#if hasBody}
			<div class="space-y-1">
				<label for="{uid}-body" class="text-xs font-medium">Body</label>
				<Textarea
					id="{uid}-body"
					rows={4}
					class="min-h-0 font-mono text-xs"
					bind:value={body}
					placeholder={'{"title": "test"}'}
					spellcheck="false"
				/>
				{#if body.trim() && headerValue(built.headers, 'content-type') === undefined}
					<p class="text-[11px] text-muted-foreground">
						{withDefaultContentType({}, body)['Content-Type'] ? 'Sent as application/json.' : 'No Content-Type header: add one if the app needs it.'}
					</p>
				{/if}
			</div>
		{/if}

		{#if problems.length > 0}
			<ul class="space-y-0.5 text-[11px] font-medium text-warning-fg">
				{#each problems as p, i (i)}<li>{p}</li>{/each}
			</ul>
		{/if}

		{#if sendError}
			<p role="alert" class="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg">{sendError}</p>
		{/if}

		{#if response}
			<section class="rounded-md border border-border bg-card" aria-label="Response" aria-live="polite">
				<div class="flex flex-wrap items-center gap-2 border-b border-border px-3 py-1.5 text-xs">
					<span class={cn('rounded border px-1.5 py-px font-mono font-medium', statusClass(response.status_code))}>
						{response.status_code ?? 'No response'}
						{response.reason}
					</span>
					{#if response.elapsed_ms !== null}<span class="font-mono text-muted-foreground tabular-nums">{response.elapsed_ms} ms</span>{/if}
					{#if responseFor}<span class="min-w-0 truncate font-mono text-muted-foreground">{responseFor.method} {responseFor.path}</span>{/if}
					{#if response.truncated}<span class="text-warning-fg">Body shortened (over 512 KB)</span>{/if}
					<Button type="button" variant="ghost" size="sm" class="ml-auto h-6 px-2 text-[11px]" onclick={copyBody} disabled={!response.body}>
						<Copy class="size-3" />
						Copy body
					</Button>
				</div>
				{#if response.error}
					<p class="border-b border-border px-3 py-1.5 text-xs text-danger-fg">{response.error}</p>
				{/if}
				{#if responseHeaders.length > 0}
					<details class="border-b border-border">
						<summary class="cursor-pointer px-3 py-1.5 text-xs font-medium">
							Response headers <span class="font-mono text-muted-foreground tabular-nums">{responseHeaders.length}</span>
						</summary>
						<dl class="grid grid-cols-[minmax(0,auto)_1fr] gap-x-3 gap-y-0.5 px-3 pb-2 font-mono text-[11px]">
							{#each responseHeaders as [name, value] (name)}
								<dt class="text-muted-foreground">{name}</dt>
								<dd class="min-w-0 break-all">{value}</dd>
							{/each}
						</dl>
					</details>
				{/if}
				{#if response.body}
					<pre class="max-h-[28rem] overflow-auto px-3 py-2 font-mono text-[11px] leading-relaxed"><code>{pretty?.text}</code></pre>
				{:else}
					<p class="px-3 py-2 text-xs text-muted-foreground">Empty body.</p>
				{/if}
			</section>
		{/if}

		{#if history.length > 0 && !showHistory}
			<div class="flex justify-end">
				<button type="button" class="inline-flex cursor-pointer items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground" onclick={clearHistory}>
					<Trash2 class="size-3" />
					Clear local history
				</button>
			</div>
		{/if}
	</div>
</form>
