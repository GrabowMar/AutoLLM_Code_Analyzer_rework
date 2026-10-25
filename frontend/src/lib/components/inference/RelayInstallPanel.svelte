<script lang="ts">
	/**
	 * RelayInstallPanel — how to run the relay on the model machine.
	 *
	 * Tabs: Python · Docker · systemd · Ollama settings, each a copyable block
	 * from `GET /inference/endpoints/{id}/install/`. Every command verifies the
	 * relay script against the sha256 shown here before running it; a command
	 * that does not carry the hash is flagged instead of offered for copying.
	 * The token never appears in a command: it is read from stdin into a 0600
	 * file.
	 *
	 * Props: `endpointId`, `class`.
	 */
	import { onMount } from 'svelte';
	import { getInstall } from '$lib/api/inference';
	import type { RelayInstall } from '$lib/api/types/inference';
	import { formatApiError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import * as Tabs from '$lib/components/ui/tabs';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import CopyBlock from './CopyBlock.svelte';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		endpointId: number;
		class?: string;
	}

	let { endpointId, class: className }: Props = $props();

	type TabKey = 'python' | 'docker' | 'systemd' | 'ollama';

	let install = $state<RelayInstall | null>(null);
	let loading = $state(true);
	let error = $state('');
	let tab = $state<TabKey>('python');

	async function load() {
		loading = true;
		error = '';
		try {
			install = await getInstall(endpointId);
		} catch (e) {
			error = formatApiError(e, 'Could not load the install instructions.');
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	const ollamaBlock = $derived.by(() => {
		if (!install) return '';
		const env = Object.entries(install.ollama_env ?? {});
		if (env.length === 0) return '';
		const vars = env.map(([k, v]) => `"${k}=${v}"`).join(' ');
		return [
			'# Keep Ollama on localhost: the relay runs on the same machine.',
			'sudo systemctl edit ollama',
			'#   [Service]',
			`#   Environment=${vars}`,
			'sudo systemctl restart ollama',
		].join('\n');
	});

	/** Commands that download the script must pin it; an unpinned one is never shown as copyable. */
	function pinned(command: string): boolean {
		return !!install && !!install.script_sha256 && command.includes(install.script_sha256);
	}

	const tabs: { value: TabKey; label: string }[] = [
		{ value: 'python', label: 'Python' },
		{ value: 'docker', label: 'Docker' },
		{ value: 'systemd', label: 'systemd' },
		{ value: 'ollama', label: 'Ollama settings' },
	];
</script>

<div class={cn('min-w-0 space-y-3', className)}>
	{#if loading}
		<LoadingState label="loading install instructions" class="py-6" />
	{:else if error}
		<ErrorAlert message={error} onretry={load} />
	{:else if install}
		<div class="flex flex-wrap items-start gap-x-4 gap-y-1 text-xs text-muted-foreground">
			<span class="inline-flex items-center gap-1.5">
				<ShieldCheck class="size-3.5 shrink-0 text-success-fg" aria-hidden="true" />
				Relay v{install.relay_version} · script sha256
			</span>
			<code class="mono-tag max-w-full break-all">{install.script_sha256}</code>
			{#if install.dockerfile_sha256}
				<span class="w-full sm:w-auto">Dockerfile sha256 <code class="mono-tag break-all">{install.dockerfile_sha256}</code></span>
			{/if}
		</div>

		<Tabs.Root bind:value={tab}>
			<Tabs.List>
				{#each tabs as t (t.value)}
					<Tabs.Trigger value={t.value}>{t.label}</Tabs.Trigger>
				{/each}
			</Tabs.List>
			{#each ['python', 'docker', 'systemd'] as const as key (key)}
				{@const command = install.commands[key]}
				<Tabs.Content value={key} class="space-y-2">
					{#if !command}
						<p class="text-xs text-muted-foreground">No {key} instructions for this endpoint.</p>
					{:else if !pinned(command)}
						<div class="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-danger-fg" role="alert">
							<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
							These commands do not verify the script hash, so they are not shown. Reload the page; if it persists,
							the server's relay files are misconfigured.
						</div>
					{:else}
						<CopyBlock text={command} label="{key} install commands" />
						<p class="text-[11px] text-muted-foreground">
							{#if key === 'python'}
								Python 3.9+, no packages needed. The <code class="mono-tag">install -m600 /dev/stdin</code> line waits
								for you to paste the token, then Ctrl-D. Run <code class="mono-tag">--check</code> first: it tests the
								server, the token and the model server without opening a session.
							{:else if key === 'docker'}
								Builds the image once from the verified script; the container never downloads code. The token goes
								into the volume from stdin.
							{:else}
								Runs the relay as a service that restarts on failure, except when the token is rejected. After
								replacing the token file, reload it with <code class="mono-tag">systemctl kill -s HUP llmlab-relay</code>.
							{/if}
						</p>
					{/if}
				</Tabs.Content>
			{/each}
			<Tabs.Content value="ollama" class="space-y-2">
				{#if ollamaBlock}
					<CopyBlock text={ollamaBlock} label="Ollama service settings" />
				{:else}
					<p class="text-xs text-muted-foreground">No server settings to apply for this endpoint kind.</p>
				{/if}
				<p class="text-[11px] text-muted-foreground">
					Match <code class="mono-tag">OLLAMA_NUM_PARALLEL</code> to the endpoint's slots and
					<code class="mono-tag">OLLAMA_MAX_LOADED_MODELS</code> to its max resident models. For strictly
					reproducible experiments keep parallel at 1: batching concurrent requests can change seeded outputs.
				</p>
			</Tabs.Content>
		</Tabs.Root>
	{/if}
</div>
