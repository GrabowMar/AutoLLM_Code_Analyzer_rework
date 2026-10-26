<script lang="ts">
	/**
	 * GitHubSection — the GitHub token (credentials API) plus the account it
	 * authenticates as. Exports start from an application's detail page
	 * (GitHubExportDialog).
	 */
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import CredentialCard from '$lib/components/users-settings/CredentialCard.svelte';
	import { formatApiError } from '$lib/api/core';
	import { getGitHubStatus, type GitHubStatus } from '$lib/api/integrations';
	import GitBranch from '@lucide/svelte/icons/git-branch';
	import RotateCw from '@lucide/svelte/icons/rotate-cw';
	import ExternalLink from '@lucide/svelte/icons/external-link';

	let status = $state<GitHubStatus | null>(null);
	let loading = $state(true);
	let error = $state('');

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

<div class="space-y-4">
	<Card.Root>
		<Card.Header>
			<Card.Title class="flex items-center justify-between gap-2">
				<span class="flex items-center gap-2"><GitBranch class="size-5" />GitHub account</span>
				<Button variant="ghost" size="sm" onclick={load} aria-label="Refresh GitHub status" title="Refresh">
					<RotateCw />
				</Button>
			</Card.Title>
			<Card.Description>
				Export generated applications to a new repository (one commit, optionally with the stack scaffold and a
				SARIF upload of the analysis findings to code scanning) or to a Gist. Open an application and choose
				<span class="font-medium text-foreground">Export to GitHub</span>.
			</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			{#if error}
				<ErrorAlert message={error} onretry={load} />
			{:else if loading}
				<LoadingState label="checking GitHub" />
			{:else if status}
				<div class="flex flex-wrap items-center gap-3">
					{#if status.avatar_url}
						<img src={status.avatar_url} alt="" class="size-9 rounded-full border border-border" />
					{/if}
					<div class="min-w-0 space-y-0.5">
						<p class="flex flex-wrap items-center gap-2 text-sm">
							{#if status.connected && status.login}
								<a
									href={status.html_url ?? `https://github.com/${status.login}`}
									target="_blank"
									rel="noopener noreferrer"
									class="inline-flex items-center gap-1 font-medium underline-offset-2 hover:underline"
								>
									{status.name || status.login}<ExternalLink class="size-3" aria-hidden="true" />
								</a>
								{#if status.name}<span class="font-mono text-xs text-muted-foreground">@{status.login}</span>{/if}
							{/if}
							<StatusBadge
								status={status.connected ? 'connected' : status.configured ? 'rejected' : 'not configured'}
								tone={status.connected ? 'success' : status.configured ? 'danger' : 'neutral'}
							/>
						</p>
						{#if status.message}<p class="text-xs text-muted-foreground">{status.message}</p>{/if}
						{#if status.connected}
							<p class="text-xs text-muted-foreground">
								{status.token_type ? `${status.token_type} token` : 'Token'}
								{#if status.scopes.length}
									· scopes
									{#each status.scopes as s (s)}<code class="mono-tag ml-1">{s}</code>{/each}
								{/if}
							</p>
						{/if}
					</div>
				</div>
			{/if}
			<div class="space-y-1 rounded-md border border-border p-3 text-xs text-muted-foreground">
				<p class="section-label">Token permissions</p>
				<p>
					Classic token: <code class="mono-tag">repo</code> (create and push repositories),
					<code class="mono-tag">gist</code>, <code class="mono-tag">security_events</code> (SARIF upload).
				</p>
				<p>
					Fine-grained token: repository access to all repositories with
					<span class="text-foreground">Administration</span> and <span class="text-foreground">Contents</span> set to
					read and write (add <span class="text-foreground">Code scanning alerts</span> write for SARIF). Fine-grained
					tokens cannot create Gists.
				</p>
				<p>
					<a
						href="https://github.com/settings/tokens"
						target="_blank"
						rel="noopener noreferrer"
						class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline"
					>
						Create a token on GitHub<ExternalLink class="size-3" aria-hidden="true" />
					</a>
				</p>
			</div>
		</Card.Content>
	</Card.Root>

	<CredentialCard
		provider="github"
		title="GitHub Token"
		description="Personal access token used only for your own exports. Encrypted at rest and never returned by the API."
		signupUrl="https://github.com/settings/tokens"
		signupLabel="github.com/settings/tokens"
		placeholder="ghp_… or github_pat_…"
		inputId="github-token"
		onchange={load}
	/>
</div>
