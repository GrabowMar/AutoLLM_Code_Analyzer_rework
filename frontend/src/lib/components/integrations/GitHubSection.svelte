<script lang="ts">
	/**
	 * GitHubSection — the GitHub token (credentials API) plus the account it
	 * authenticates as. Exports start from an application's detail page
	 * (GitHubExportDialog).
	 */
	import { onMount } from 'svelte';
	import SettingsBlock from '$lib/components/users-settings/SettingsBlock.svelte';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import CredentialRow from '$lib/components/users-settings/CredentialRow.svelte';
	import { useSectionStatus } from '$lib/components/users-settings/section-status.svelte';
	import { formatApiError } from '$lib/api/core';
	import { getGitHubStatus, type GitHubStatus } from '$lib/api/integrations';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import GitBranch from '@lucide/svelte/icons/git-branch';

	let status = $state<GitHubStatus | null>(null);
	let loading = $state(true);
	let error = $state('');

	const report = useSectionStatus('github');
	$effect(() => {
		if (!status) return report(null);
		if (status.connected) report({ tone: 'success', label: 'connected' });
		else if (status.configured) report({ tone: 'danger', label: 'rejected' });
		else report(null);
	});

	async function load() {
		loading = status === null;
		error = '';
		try {
			status = await getGitHubStatus();
		} catch (e) {
			error = formatApiError(e, 'Could not check the GitHub connection.');
		} finally {
			loading = false;
		}
	}

	onMount(load);
</script>

<SettingsBlock title="Connection">
	{#snippet description()}
		Exports are one commit, optionally with the stack scaffold and a SARIF upload to code scanning. Start one from
		an application with <span class="font-medium text-foreground">Export to GitHub</span>.
	{/snippet}

	<div class="settings-row flex items-center justify-between gap-4">
		{#if error}
			<div class="min-w-0 flex-1"><ErrorAlert message={error} onretry={load} /></div>
		{:else if loading}
			<div class="h-9 w-56 animate-pulse rounded bg-muted motion-reduce:animate-none"></div>
		{:else if status}
			<div class="flex min-w-0 items-center gap-3">
				{#if status.avatar_url}
					<img src={status.avatar_url} alt="" class="size-9 shrink-0 rounded-full border border-border" />
				{:else}
					<span class="flex size-9 shrink-0 items-center justify-center rounded-full border border-border bg-muted text-muted-foreground">
						<GitBranch class="size-4" aria-hidden="true" />
					</span>
				{/if}
				<div class="min-w-0 space-y-0.5">
					<p class="flex flex-wrap items-center gap-2 text-sm font-medium">
						{#if status.connected && status.login}
							<a
								href={status.html_url ?? `https://github.com/${status.login}`}
								target="_blank"
								rel="noopener noreferrer"
								class="inline-flex items-center gap-1 underline-offset-2 hover:underline"
							>
								{status.name || status.login}<ExternalLink class="size-3" aria-hidden="true" />
							</a>
							{#if status.name}<span class="font-mono text-xs font-normal text-muted-foreground">@{status.login}</span>{/if}
						{:else}
							<span>{status.configured ? 'Token rejected' : 'Not connected'}</span>
						{/if}
						{#if status.configured}
							<StatusBadge
								status={status.connected ? 'connected' : 'rejected'}
								tone={status.connected ? 'success' : 'danger'}
							/>
						{/if}
					</p>
					<p class="text-xs text-muted-foreground">
						{#if status.connected}
							{status.token_type ? `${status.token_type} token` : 'Token'}
							{#if status.scopes.length}
								· {#each status.scopes as s (s)}<code class="mono-tag ml-1">{s}</code>{/each}
							{/if}
						{:else}
							{status.message || 'Add a personal access token below.'}
						{/if}
					</p>
				</div>
			</div>
		{/if}
		<Button variant="ghost" size="sm" class="size-8 shrink-0 p-0" onclick={load} aria-label="Refresh GitHub status" title="Refresh">
			<RotateCw />
		</Button>
	</div>

	<CredentialRow
		provider="github"
		title="Personal access token"
		description="Used only for your own exports. Create one at"
		signupUrl="https://github.com/settings/tokens"
		signupLabel="github.com/settings/tokens"
		placeholder="ghp_… or github_pat_…"
		inputId="github-token"
		onchange={load}
	/>

	<details class="group">
		<summary
			class="settings-row flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium transition-colors hover:bg-muted/30 [&::-webkit-details-marker]:hidden"
		>
			Which permissions does the token need?
			<ChevronDown class="size-4 text-muted-foreground transition-transform duration-150 group-open:rotate-180" aria-hidden="true" />
		</summary>
		<div class="space-y-2 px-4 pb-4 text-[13px] leading-5 text-muted-foreground">
			<p>
				<span class="font-medium text-foreground">Classic token:</span> <code class="mono-tag">repo</code> (create and
				push repositories), <code class="mono-tag">gist</code>, <code class="mono-tag">security_events</code> (SARIF upload).
			</p>
			<p>
				<span class="font-medium text-foreground">Fine-grained token:</span> access to all repositories with
				Administration and Contents set to read and write (add Code scanning alerts write for SARIF). Fine-grained
				tokens cannot create Gists.
			</p>
		</div>
	</details>
</SettingsBlock>
