<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import { getAppTemplates, type AppRequirementTemplate } from '$lib/api/generation';
	import { createExperiment } from '$lib/api/experiments';
	import { slugify } from '$lib/components/templates/slugify';
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Search from '@lucide/svelte/icons/search';

	let name = $state('');
	let slug = $state('');
	let slugTouched = $state(false);
	let description = $state('');
	let hypothesis = $state('');
	let repeats = $state(3);
	let baseSeed = $state<string>('');
	let temperature = $state(0.3);
	let maxTokens = $state(32000);

	let apps = $state<AppRequirementTemplate[]>([]);
	let appsLoading = $state(true);
	let selectedAppIds = $state<Set<number>>(new Set());
	let appSearch = $state('');
	let categoryFilter = $state('');

	let submitting = $state(false);
	let error = $state('');

	onMount(async () => {
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
				temperature,
				max_tokens: maxTokens,
			});
			goto(`/experiments/${experiment.id}`);
		} catch (err: any) {
			error = err?.detail ?? err?.message ?? 'Failed to create experiment';
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>New Experiment - LLM Lab</title>
</svelte:head>

<div class="mx-auto max-w-3xl space-y-6">
	<Button variant="ghost" size="sm" onclick={() => goto('/experiments')}>
		<ArrowLeft class="h-4 w-4" />
		Back to experiments
	</Button>

	<div>
		<h1 class="text-2xl font-semibold tracking-tight">New experiment</h1>
		<p class="text-sm text-muted-foreground">
			Pick the apps to test; conditions (model × prompt bundle) and launch happen on the next page.
		</p>
	</div>

	<Card.Root>
		<Card.Header>
			<Card.Title>Basics</Card.Title>
		</Card.Header>
		<Card.Content class="space-y-4">
			<div class="grid gap-4 sm:grid-cols-2">
				<div class="space-y-1.5">
					<Label for="exp-name">Name</Label>
					<Input id="exp-name" bind:value={name} placeholder="Tone ablation on recipe apps" />
				</div>
				<div class="space-y-1.5">
					<Label for="exp-slug">Slug</Label>
					<Input
						id="exp-slug"
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
			<div class="grid gap-4 sm:grid-cols-3">
				<div class="space-y-1.5">
					<Label for="exp-repeats">Repeats per cell</Label>
					<input
						id="exp-repeats"
						type="number"
						min={1}
						max={20}
						bind:value={repeats}
						class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
					/>
					<p class="text-[10px] text-muted-foreground">Independent trials — comparisons need n &gt; 1.</p>
				</div>
				<div class="space-y-1.5">
					<Label for="exp-seed">Base seed (optional)</Label>
					<Input id="exp-seed" type="number" bind:value={baseSeed} placeholder="random" />
					<p class="text-[10px] text-muted-foreground">Set for a reproducible seed matrix across launches.</p>
				</div>
				<div class="space-y-1.5">
					<Label for="exp-temp">Default temperature</Label>
					<input
						id="exp-temp"
						type="number"
						step="0.1"
						min={0}
						max={2}
						bind:value={temperature}
						class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
					/>
				</div>
			</div>
		</Card.Content>
	</Card.Root>

	<Card.Root>
		<Card.Header class="pb-3">
			<div class="flex items-center justify-between">
				<Card.Title>Apps</Card.Title>
				<span class="text-xs text-muted-foreground">{selectedAppIds.size} selected</span>
			</div>
		</Card.Header>
		<Card.Content class="space-y-3">
			<div class="flex flex-wrap items-center gap-2">
				<div class="relative flex-1 min-w-[180px]">
					<Search class="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
					<Input bind:value={appSearch} placeholder="Search apps..." class="pl-8 h-8 text-sm" />
				</div>
				<select bind:value={categoryFilter} class="h-8 rounded-md border border-input bg-surface-1 px-2 text-sm">
					<option value="">All categories</option>
					{#each categories as cat (cat)}
						<option value={cat}>{cat}</option>
					{/each}
				</select>
				<Button variant="outline" size="sm" onclick={selectAllFiltered}>Select all</Button>
				<Button variant="outline" size="sm" onclick={clearApps}>Clear</Button>
			</div>

			{#if appsLoading}
				<div class="flex justify-center py-8"><LoaderCircle class="h-5 w-5 animate-spin text-muted-foreground" /></div>
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
								class="mt-0.5"
								checked={selectedAppIds.has(app.id)}
								onchange={() => toggleApp(app.id)}
							/>
							<span>
								<span class="font-medium">{app.name}</span>
								{#if app.category}
									<span class="ml-1.5 text-[10px] text-muted-foreground">{app.category}</span>
								{/if}
							</span>
						</label>
					{/each}
				</div>
			{/if}
		</Card.Content>
	</Card.Root>

	{#if error}
		<div class="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}</div>
	{/if}

	<div class="flex justify-end gap-2">
		<Button variant="outline" onclick={() => goto('/experiments')}>Cancel</Button>
		<Button onclick={submit} disabled={submitting || !name.trim() || !slug.trim()}>
			{#if submitting}<LoaderCircle class="h-4 w-4 animate-spin" />{/if}
			Create experiment
		</Button>
	</div>
</div>
