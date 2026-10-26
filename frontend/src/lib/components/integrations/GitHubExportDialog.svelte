<script lang="ts">
	/**
	 * GitHubExportDialog — export one application (generation job) to a new
	 * GitHub repository or a Gist.
	 *
	 * Props: `open` (bindable), `applicationId` (GenerationJob UUID),
	 * `defaultName` (suggested repository name; slugified).
	 * Checks `/integrations/github/status/` on open and points to the
	 * Settings → GitHub when no working token is stored.
	 */
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Segmented } from '$lib/components/ui/segmented';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import CopyBlock from '$lib/components/inference/CopyBlock.svelte';
	import { formatApiError } from '$lib/api/core';
	import {
		exportToGitHub,
		getGitHubStatus,
		type GitHubExportResult,
		type GitHubExportTarget,
		type GitHubStatus,
	} from '$lib/api/integrations';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Upload from '@lucide/svelte/icons/upload';

	interface Props {
		open?: boolean;
		applicationId: string;
		defaultName?: string;
	}

	let { open = $bindable(false), applicationId, defaultName = '' }: Props = $props();

	const REPO_NAME = /^[A-Za-z0-9._-]{1,100}$/;

	function slugify(text: string): string {
		return (
			text
				.toLowerCase()
				.replace(/[^a-z0-9._-]+/g, '-')
				.replace(/^-+|-+$/g, '')
				.slice(0, 80) || 'llm-lab-app'
		);
	}

	let status = $state<GitHubStatus | null>(null);
	let statusLoading = $state(false);
	let statusError = $state('');

	let target = $state<GitHubExportTarget>('repo');
	let repoName = $state('');
	let isPrivate = $state(true);
	let description = $state('');
	let uploadSarif = $state(true);
	let includeScaffold = $state(true);

	let exporting = $state(false);
	let error = $state('');
	let result = $state<GitHubExportResult | null>(null);

	const repoNameValid = $derived(target === 'gist' || REPO_NAME.test(repoName.trim()));
	const canExport = $derived(!!status?.connected && !exporting && repoNameValid);

	async function loadStatus() {
		statusLoading = true;
		statusError = '';
		try {
			status = await getGitHubStatus();
		} catch (e) {
			statusError = formatApiError(e, 'Could not check the GitHub connection.');
		} finally {
			statusLoading = false;
		}
	}

	// Each open starts from a fresh form and a fresh token check.
	$effect(() => {
		if (!open) return;
		result = null;
		error = '';
		repoName = slugify(defaultName || `llm-lab-${applicationId.slice(0, 8)}`);
		void loadStatus();
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!canExport) return;
		exporting = true;
		error = '';
		try {
			result = await exportToGitHub({
				application_id: applicationId,
				target,
				...(target === 'repo' ? { repo_name: repoName.trim() } : {}),
				private: isPrivate,
				...(description.trim() ? { description: description.trim() } : {}),
				upload_sarif: target === 'repo' && uploadSarif,
				include_scaffold: target === 'repo' && includeScaffold,
			});
		} catch (e) {
			error = formatApiError(e, 'Export failed.');
		} finally {
			exporting = false;
		}
	}
</script>

<Dialog.Root
	{open}
	onOpenChange={(next) => {
		if (!exporting || next) open = next;
	}}
>
	<Dialog.Content class="max-h-[90vh] max-w-lg overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Export to GitHub</Dialog.Title>
			<Dialog.Description>
				Push the generated code to a new repository (one commit) or a Gist.
			</Dialog.Description>
		</Dialog.Header>

		<div class="mt-4 space-y-4">
			{#if statusLoading && !status}
				<LoadingState label="checking GitHub" />
			{:else if statusError}
				<ErrorAlert message={statusError} onretry={loadStatus} />
			{:else if status && !status.connected}
				<div class="space-y-2 rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning-fg">
					<p>
						{status.configured
							? status.message || 'GitHub rejected the stored token.'
							: 'No GitHub token is stored yet.'}
					</p>
					<Button size="sm" variant="outline" href="/users/settings#github">Set up GitHub</Button>
				</div>
			{/if}

			{#if result}
				<div class="space-y-3" role="status">
					<p class="flex items-center gap-1.5 text-sm text-success-fg">
						<CircleCheck class="size-4" aria-hidden="true" />
						Exported {result.files_count} file{result.files_count === 1 ? '' : 's'}
						{result.target === 'gist' ? 'to a Gist' : `to ${result.repo_full_name ?? 'GitHub'}`}.
					</p>
					<CopyBlock text={result.url} label="export URL" wrap />
					<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
						{#if result.commit_sha}
							<span>Commit <code class="mono-tag">{result.commit_sha.slice(0, 12)}</code></span>
						{/if}
						{#if result.target === 'repo'}
							<span class={result.sarif.uploaded ? 'text-success-fg' : ''}>SARIF: {result.sarif.message}</span>
						{/if}
					</div>
					{#if result.skipped.length > 0}
						<details class="text-xs text-muted-foreground">
							<summary class="cursor-pointer">{result.skipped.length} file{result.skipped.length === 1 ? '' : 's'} skipped</summary>
							<ul class="mt-1 max-h-32 space-y-0.5 overflow-y-auto font-mono">
								{#each result.skipped as f (f)}<li class="break-all">{f}</li>{/each}
							</ul>
						</details>
					{/if}
					<Dialog.Footer class="gap-2">
						<Button variant="outline" size="sm" onclick={() => (open = false)}>Close</Button>
						<Button size="sm" href={result.url} target="_blank" rel="noopener noreferrer">
							Open on GitHub<ExternalLink />
						</Button>
					</Dialog.Footer>
				</div>
			{:else}
				<form class="space-y-4" onsubmit={submit}>
					{#if status?.connected && status.login}
						<p class="text-xs text-muted-foreground">
							As <span class="font-medium text-foreground">{status.login}</span>
						</p>
					{/if}
					<Segmented
						aria-label="Export target"
						bind:value={target}
						fullWidth
						items={[
							{ value: 'repo', label: 'New repository' },
							{ value: 'gist', label: 'Gist' },
						]}
					/>
					{#if target === 'repo'}
						<div class="space-y-1.5">
							<Label for="gh-repo-name">Repository name</Label>
							<Input
								id="gh-repo-name"
								bind:value={repoName}
								maxlength={100}
								autocomplete="off"
								spellcheck={false}
								aria-invalid={!repoNameValid}
								aria-describedby="gh-repo-name-help"
							/>
							<p id="gh-repo-name-help" class="text-xs {repoNameValid ? 'text-muted-foreground' : 'text-danger-fg'}">
								Letters, digits, <code>.</code>, <code>-</code> and <code>_</code>. The repository must not exist yet.
							</p>
						</div>
					{/if}
					<div class="space-y-1.5">
						<Label for="gh-description">Description <span class="text-muted-foreground">(optional)</span></Label>
						<Input id="gh-description" bind:value={description} maxlength={350} />
					</div>
					<div class="space-y-2 text-sm">
						<label class="flex cursor-pointer items-center gap-2">
							<Checkbox bind:checked={isPrivate} />
							{target === 'gist' ? 'Secret gist' : 'Private repository'}
						</label>
						{#if target === 'repo'}
							<label class="flex cursor-pointer items-center gap-2">
								<Checkbox bind:checked={includeScaffold} />
								Include the stack scaffold (so the app builds)
							</label>
							<label class="flex cursor-pointer items-start gap-2">
								<Checkbox bind:checked={uploadSarif} class="mt-0.5" />
								<span>
									Upload analysis findings as SARIF to code scanning
									<span class="block text-xs text-muted-foreground">
										Needs the <code class="mono-tag">security_events</code> scope; private repositories need GitHub
										Advanced Security.
									</span>
								</span>
							</label>
						{/if}
					</div>
					{#if error}<ErrorAlert message={error} />{/if}
					<Dialog.Footer class="gap-2">
						<Button type="button" variant="outline" size="sm" onclick={() => (open = false)} disabled={exporting}>
							Cancel
						</Button>
						<Button type="submit" size="sm" disabled={!canExport} aria-busy={exporting}>
							{#if exporting}<LoaderCircle class="animate-spin" />{:else}<Upload />{/if}
							Export
						</Button>
					</Dialog.Footer>
				</form>
			{/if}
		</div>
	</Dialog.Content>
</Dialog.Root>
