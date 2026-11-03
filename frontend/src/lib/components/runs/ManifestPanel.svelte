<script lang="ts">
	/**
	 * ManifestPanel — the frozen RunManifest of a sample (spec 3.8, 10.4): the factors with their pinned refs
	 * (each links to that exact Library version), the stimuli with their texts and source spans, and the whole
	 * manifest as a tree. Everything the sample depends on is in here (Appendix A #1).
	 *
	 * Props: `runId`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import { formatApiError, isAbortError } from '$lib/api/core';
	import { getSampleManifest } from '$lib/api/v3/samples';
	import type { RunManifestOut } from '$lib/api/v3/types';
	import FileJson from '@lucide/svelte/icons/file-json';
	import JsonTree from './JsonTree.svelte';
	import { libraryHref } from './refs';
	import { segmentBySources } from './spans';
	import { shortSha } from './format';

	interface Props {
		runId: string;
	}

	let { runId }: Props = $props();

	let data = $state<RunManifestOut | null>(null);
	let loading = $state(true);
	let error = $state('');
	let missing = $state(false);

	async function load(id: string, signal: AbortSignal) {
		loading = true;
		error = '';
		missing = false;
		try {
			data = await getSampleManifest(id, { signal });
		} catch (e) {
			if (isAbortError(e)) return;
			if ((e as { httpStatus?: number })?.httpStatus === 404) missing = true;
			else error = formatApiError(e, 'Could not load the manifest.');
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		const controller = new AbortController();
		void load(runId, controller.signal);
		return () => controller.abort();
	});

	type FactorRecord = { ref?: string | null; content_hash?: string | null; value?: string | null };
	const factors = $derived(
		Object.entries((data?.manifest?.factors ?? {}) as Record<string, FactorRecord>).sort(([a], [b]) =>
			a.localeCompare(b),
		),
	);
	const identity = $derived(
		Object.entries((data?.manifest?.identity ?? {}) as Record<string, string>).filter(([, v]) => !!v),
	);
	let openStimulus = $state<number | null>(0);
</script>

{#if loading}
	<LoadingState label="loading manifest" />
{:else if error}
	<ErrorAlert message={error} onretry={() => load(runId, new AbortController().signal)} />
{:else if missing || !data}
	<EmptyState
		icon={FileJson}
		title="No manifest yet"
		description="The manifest is frozen when the run starts; reference runs created directly in evaluation may have none."
	/>
{:else}
	<div class="space-y-4">
		<div class="grid gap-4 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="font-display">Factors</Card.Title>
					<Card.Description>Every factor of the cell, pinned by ref and content hash.</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table>
							<thead><tr><th>Factor</th><th>Level</th><th>Content hash</th></tr></thead>
							<tbody>
								{#each factors as [key, record] (key)}
									{@const href = record.ref ? libraryHref(key, record.ref) : null}
									<tr>
										<td class="font-mono text-xs">{key}</td>
										<td class="font-mono text-xs">
											{#if href}
												<a class="text-primary hover:underline" {href}>{record.ref}</a>
											{:else}
												{record.ref ?? record.value ?? '—'}
											{/if}
										</td>
										<td class="font-mono text-xs text-muted-foreground" title={record.content_hash ?? ''}>
											{shortSha(record.content_hash)}
										</td>
									</tr>
								{:else}
									<tr><td colspan="3" class="text-muted-foreground">No factors recorded.</td></tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>

			<Card.Root>
				<Card.Header>
					<Card.Title class="font-display">Identity</Card.Title>
					<Card.Description>
						Manifest hash <span class="font-mono" title={data.manifest_hash}>{shortSha(data.manifest_hash, 16)}</span>
					</Card.Description>
				</Card.Header>
				<Card.Content>
					<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
						{#each identity as [key, value] (key)}
							<dt class="text-muted-foreground">{key.replace(/_/g, ' ')}</dt>
							<dd class="truncate font-mono" title={value}>{shortSha(value, 20)}</dd>
						{/each}
					</dl>
				</Card.Content>
			</Card.Root>
		</div>

		<SectionHeading size="sm" title="Stimuli" count={data.stimuli.length} />
		<div class="space-y-2">
			{#each data.stimuli as stimulus, i (i)}
				{@const href = libraryHref('prompt', stimulus.template_ref)}
				<Card.Root class="gap-0 py-0">
					<button
						type="button"
						class="flex w-full cursor-pointer flex-wrap items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-accent/40"
						aria-expanded={openStimulus === i}
						onclick={() => (openStimulus = openStimulus === i ? null : i)}
					>
						<Badge variant="outline" class="font-mono">{stimulus.role}</Badge>
						<span class="font-mono text-xs text-muted-foreground">{stimulus.template_ref}</span>
						<span class="ml-auto font-mono text-xs text-muted-foreground" title={stimulus.sha}>{shortSha(stimulus.sha)}</span>
					</button>
					{#if openStimulus === i}
						<div class="space-y-2 border-t border-border px-4 py-3">
							{#if href}
								<a class="text-xs text-primary hover:underline" {href}>Open the template version in the Library</a>
							{/if}
							{#if stimulus.text === null}
								<p class="text-sm text-muted-foreground">The text is missing from the blob store (only its sha is recorded).</p>
							{:else}
								<pre class="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded border border-border bg-surface-2 p-3 font-mono text-xs leading-5">{#each segmentBySources(stimulus.text, stimulus.sources) as segment, s (s)}{#if segment.source}<span
												class="stimulus-span"
												title={`${segment.source.owner_kind} ${segment.source.owner_ref} (${segment.source.template_ref})`}
												>{segment.text}</span
											>{:else}{segment.text}{/if}{/each}</pre>
								{#if stimulus.sources?.length}
									<p class="text-[11px] text-muted-foreground">Underlined text comes from a library entity; hover to see which.</p>
								{/if}
							{/if}
						</div>
					{/if}
				</Card.Root>
			{:else}
				<p class="text-sm text-muted-foreground">No stimuli recorded.</p>
			{/each}
		</div>

		<SectionHeading size="sm" title="Full manifest" />
		<Card.Root>
			<Card.Content class="overflow-x-auto">
				<JsonTree value={data.manifest} openDepth={1} />
			</Card.Content>
		</Card.Root>
	</div>
{/if}

<style>
	.stimulus-span {
		text-decoration: underline;
		text-decoration-color: color-mix(in oklab, var(--primary) 55%, transparent);
		text-underline-offset: 3px;
	}
</style>
