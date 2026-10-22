<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { getAppTemplates, type AppRequirementTemplate, type LLMParams } from '$lib/api/generation';
	import { createExperiment, type ExperimentMode } from '$lib/api/experiments';
	import { getAgentHarnesses, type AgentConfig, type AgentHarness } from '$lib/api/generation';
	import LLMParamsEditor from '$lib/components/sample-generator/LLMParamsEditor.svelte';
	import { slugify } from '$lib/components/templates/slugify';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Search from '@lucide/svelte/icons/search';

	let name = $state('');
	let slug = $state('');
	let slugTouched = $state(false);
	let description = $state('');
	let hypothesis = $state('');
	let repeats = $state(3);
	let baseSeed = $state<string>('');
	let llmDefaults = $state<LLMParams>({});
	let mode = $state<ExperimentMode>('agent');
	// '' = server default (AGENT_* settings)
	let wallMinutes = $state<number | ''>('');
	let maxRequests = $state<number | ''>('');
	let maxCost = $state<number | ''>('');
	let harness = $state('');
	let samplingPolicy = $state<'' | 'provider_default' | 'harness_native'>('');
	let harnesses = $state<AgentHarness[]>([]);

	let apps = $state<AppRequirementTemplate[]>([]);
	let appsLoading = $state(true);
	let selectedAppIds = $state<Set<number>>(new Set());
	let appSearch = $state('');
	let categoryFilter = $state('');

	let submitting = $state(false);
	let error = $state('');

	onMount(async () => {
		getAgentHarnesses()
			.then((list) => (harnesses = list))
			.catch(() => {});
		try {
			apps = await getAppTemplates();
		} catch {
			// list still usable without apps preloaded; user can retry via reload
		} finally {
			appsLoading = false;
		}
	});

	$effect(() => {
		if (!slugTouched) slug = slugify(name);
	});

	const categories = $derived([...new Set(apps.map((a) => a.category).filter(Boolean))].sort());

	const filteredApps = $derived(
		apps.filter((a) => {
			if (categoryFilter && a.category !== categoryFilter) return false;
			if (!appSearch) return true;
			const q = appSearch.toLowerCase();
			return a.name.toLowerCase().includes(q) || (a.description ?? '').toLowerCase().includes(q);
		}),
	);

	function toggleApp(id: number) {
		const next = new Set(selectedAppIds);
		next.has(id) ? next.delete(id) : next.add(id);
		selectedAppIds = next;
	}

	function selectAllFiltered() {
		selectedAppIds = new Set(filteredApps.map((a) => a.id));
	}

	function clearApps() {
		selectedAppIds = new Set();
	}

	function agentConfig(): AgentConfig {
		const config: AgentConfig = {};
		if (wallMinutes !== '') config.wall_clock_seconds = Math.round(wallMinutes * 60);
		if (maxRequests !== '') config.max_llm_requests = maxRequests;
		if (maxCost !== '') config.max_cost_usd = maxCost;
		if (harness) config.harness = harness;
		if (samplingPolicy) config.sampling_policy = samplingPolicy;
		return config;
	}

	async function submit() {
		if (!name.trim() || !slug.trim()) return;
		submitting = true;
		error = '';
		try {
			const experiment = await createExperiment({
				name,
				slug,
				description,
				hypothesis,
				app_requirement_ids: [...selectedAppIds],
				repeats,
				base_seed: baseSeed.trim() ? Number(baseSeed) : null,
				llm_defaults: llmDefaults,
				mode,
				agent_config: mode === 'agent' ? agentConfig() : {},
			});
			goto(`/experiments/${experiment.id}`);
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to create experiment';
		} finally {
			submitting = false;
		}
	}
</script>

<div class="mx-auto max-w-3xl space-y-6">
	<PageHeader
		title="New experiment"
		description="Pick the apps to test; conditions (model × prompt bundle) and launch happen on the next page."
		back={{ href: '/experiments', label: 'Experiments' }}
	/>

	<Card.Root>
		<Card.Header>
			<Card.Title>Basics</Card.Title>
			<Card.Description>Name the experiment and record what it should show.</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="exp-name">Name <span class="text-destructive" aria-hidden="true">*</span></Label>
					<Input id="exp-name" bind:value={name} required placeholder="Tone ablation on recipe apps" />
				</div>
				<div class="space-y-1.5">
					<Label for="exp-slug">Slug <span class="text-destructive" aria-hidden="true">*</span></Label>
					<Input
						id="exp-slug"
						required
						bind:value={slug}
						oninput={() => (slugTouched = true)}
						placeholder="tone-ablation"
					/>
				</div>
			</div>
			<div class="space-y-1.5">
				<Label for="exp-description">Description</Label>
				<Textarea id="exp-description" bind:value={description} rows={2} />
			</div>
			<div class="space-y-1.5">
				<Label for="exp-hypothesis">Hypothesis (optional)</Label>
				<Textarea
					id="exp-hypothesis"
					bind:value={hypothesis}
					rows={2}
					placeholder="What this experiment is meant to show, for the eventual report."
				/>
			</div>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Run settings</Card.Title>
			<Card.Description>Trials per cell, seeding and LLM defaults shared by every condition.</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-4">
			<div class="space-y-1.5">
				<Label for="exp-mode">Generation method</Label>
				<select id="exp-mode" bind:value={mode} class="std-select h-9 w-full px-2" aria-describedby="exp-mode-help">
					<option value="agent">Agent: sandboxed coding agent (OpenCode) builds each app</option>
					<option value="scaffolding">Scaffolding: template prompts fill a fixed skeleton</option>
				</select>
				<p id="exp-mode-help" class="text-xs text-muted-foreground">
					{#if mode === 'agent'}
						Every condition gets the same brief, tools, system prompt and budget; a condition's profile only supplies its stack and LLM config.
					{:else}
						Prompts are rendered from each condition's profile blocks.
					{/if}
				</p>
			</div>
			{#if mode === 'agent'}
				<div class="grid gap-4 sm:grid-cols-2">
					<div class="space-y-1.5">
						<Label for="exp-harness">Default harness</Label>
						<select id="exp-harness" bind:value={harness} class="std-select h-9 w-full px-2">
							<option value="">Server default (OpenCode)</option>
							{#each harnesses as h (h.name)}
								<option value={h.name}>{h.label} {h.version}</option>
							{/each}
						</select>
						<p class="text-xs text-muted-foreground">Conditions can pick their own harness for model × harness designs.</p>
					</div>
					<div class="space-y-1.5">
						<Label for="exp-sampling">Sampling</Label>
						<select id="exp-sampling" bind:value={samplingPolicy} class="std-select h-9 w-full px-2">
							<option value="">Default (provider defaults)</option>
							<option value="provider_default">Provider defaults: strip harness-sent sampling</option>
							<option value="harness_native">Harness native: pass harness sampling through</option>
						</select>
					</div>
				</div>
				<div class="grid gap-4 sm:grid-cols-3">
					<div class="space-y-1.5">
						<Label for="exp-wall">Wall clock per run (min)</Label>
						<input id="exp-wall" type="number" min={1} max={240} bind:value={wallMinutes} placeholder="default 60" class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30" />
					</div>
					<div class="space-y-1.5">
						<Label for="exp-requests">Max LLM requests per run</Label>
						<input id="exp-requests" type="number" min={1} max={2000} bind:value={maxRequests} placeholder="default 100" class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30" />
					</div>
					<div class="space-y-1.5">
						<Label for="exp-cost">Max cost per run (USD)</Label>
						<input id="exp-cost" type="number" min={0.001} step={0.05} bind:value={maxCost} placeholder="default 5.00" class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30" />
					</div>
					<p class="sm:col-span-3 text-xs text-muted-foreground">The request budget binds; wall clock and cost are safety nets (a cost cap gives expensive models fewer steps). Runs that hit a limit still count as samples with their termination recorded.</p>
				</div>
			{/if}
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="exp-repeats">Repeats per cell</Label>
					<input
						id="exp-repeats"
						type="number"
						min={1}
						max={20}
						bind:value={repeats}
						aria-describedby="exp-repeats-help"
						class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
					/>
					<p id="exp-repeats-help" class="text-xs text-muted-foreground">Independent trials — comparisons need n &gt; 1.</p>
				</div>
				<div class="space-y-1.5">
					<Label for="exp-seed">Base seed (optional)</Label>
					<Input id="exp-seed" type="number" bind:value={baseSeed} placeholder="random" aria-describedby="exp-seed-help" />
					<p id="exp-seed-help" class="text-xs text-muted-foreground">Set for a reproducible seed matrix across launches.</p>
				</div>
			</div>
			<div class="space-y-1.5">
				<p class="text-sm font-medium">LLM defaults</p>
				<p class="text-xs text-muted-foreground">Applied to every condition; conditions can override per cell.</p>
				<LLMParamsEditor bind:params={llmDefaults} idPrefix="exp-defaults" />
			</div>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header>
			<Card.Title>Apps</Card.Title>
			<Card.Description>{selectedAppIds.size} of {apps.length} selected</Card.Description>
		</Card.Header>
		<Card.Content class="space-y-3">
			<div class="flex flex-wrap items-center gap-2">
				<div class="relative flex-1 min-w-[180px]">
					<Search class="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
					<Input bind:value={appSearch} placeholder="Search apps…" aria-label="Search apps" class="pl-8 h-8 text-sm" />
				</div>
				<select bind:value={categoryFilter} aria-label="Filter by category" class="std-select h-8 px-2">
					<option value="">All categories</option>
					{#each categories as cat (cat)}
						<option value={cat}>{cat}</option>
					{/each}
				</select>
				<Button variant="outline" size="sm" onclick={selectAllFiltered}>Select all</Button>
				<Button variant="outline" size="sm" onclick={clearApps}>Clear</Button>
			</div>

			{#if appsLoading}
				<LoadingState class="py-8" label="loading apps" />
			{:else if filteredApps.length === 0}
				<EmptyState
					compact
					title={apps.length === 0 ? 'No app templates available' : 'No matching apps'}
					description={apps.length === 0 ? 'Reload the page to retry, or add app templates first.' : 'Try a different search or category.'}
				/>
			{:else}
				<div class="grid max-h-80 gap-1.5 overflow-y-auto sm:grid-cols-2">
					{#each filteredApps as app (app.id)}
						<label
							class="flex cursor-pointer items-start gap-2 rounded-md border px-2.5 py-2 text-sm transition-colors {selectedAppIds.has(
								app.id,
							)
								? 'border-primary/50 bg-primary/5'
								: 'border-transparent hover:bg-muted/40'}"
						>
							<input
								type="checkbox"
								class="mt-0.5 accent-primary"
								checked={selectedAppIds.has(app.id)}
								onchange={() => toggleApp(app.id)}
							/>
							<span>
								<span class="font-medium">{app.name}</span>
								{#if app.category}
									<span class="ml-1.5 text-xs text-muted-foreground">{app.category}</span>
								{/if}
							</span>
						</label>
					{/each}
				</div>
			{/if}
		</Card.Content>
	</Card.Root>

	{#if error}
		<ErrorAlert message={error} />
	{/if}

	<div class="flex justify-end gap-2">
		<Button variant="outline" onclick={() => goto('/experiments')}>Cancel</Button>
		<Button onclick={submit} disabled={submitting || !name.trim() || !slug.trim()}>
			{#if submitting}<LoaderCircle class="h-4 w-4 animate-spin" />{/if}
			Create experiment
		</Button>
	</div>
</div>
