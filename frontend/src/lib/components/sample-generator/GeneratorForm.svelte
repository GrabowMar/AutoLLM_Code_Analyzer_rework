<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Label } from '$lib/components/ui/label';
	import { Separator } from '$lib/components/ui/separator';
	import { Switch } from '$lib/components/ui/switch';
	import type {
		LLMModelSummary,
		Stack,
		AppRequirementTemplate,
	} from '$lib/api/client';
	import Play from '@lucide/svelte/icons/play';
	import Search from '@lucide/svelte/icons/search';
	import Check from '@lucide/svelte/icons/check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Bot from '@lucide/svelte/icons/bot';
	import ModelSelector from '$lib/components/sample-generator/ModelSelector.svelte';

	type TabId = 'custom' | 'scaffolding' | 'copilot';

	export interface CustomPayload {
		model_id: number;
		system_prompt: string;
		user_prompt: string;
		temperature: number;
		max_tokens: number;
	}

	export interface ScaffoldingPayload {
		stack_slug: string;
		app_requirement_ids: number[];
		model_ids: number[];
		temperature: number;
		max_tokens: number;
		trials: number;
	}

	export interface CopilotPayload {
		description: string;
		model_id?: number;
		max_iterations: number;
		use_open_source: boolean;
	}

	interface Props {
		activeTab: TabId;
		models: LLMModelSummary[];
		modelsLoading: boolean;
		stacks: Stack[];
		appTemplates: AppRequirementTemplate[];
		scaffoldingLoading: boolean;
		customSubmitting: boolean;
		customError: string;
		scaffoldingSubmitting: boolean;
		scaffoldingError: string;
		scaffoldingResult: { batch_id: string; job_count: number; status: string } | null;
		copilotSubmitting: boolean;
		copilotError: string;
		customModelId?: number | '';
		copilotModelId?: number | '';
		onSubmitCustom: (payload: CustomPayload) => void;
		onSubmitScaffolding: (payload: ScaffoldingPayload) => void;
		onSubmitCopilot: (payload: CopilotPayload) => void;
	}

	let {
		activeTab,
		models,
		modelsLoading,
		stacks,
		appTemplates,
		scaffoldingLoading,
		customSubmitting,
		customError,
		scaffoldingSubmitting,
		scaffoldingError,
		scaffoldingResult,
		copilotSubmitting,
		copilotError,
		customModelId = $bindable('' as number | ''),
		copilotModelId = $bindable('' as number | ''),
		onSubmitCustom,
		onSubmitScaffolding,
		onSubmitCopilot,
	}: Props = $props();

	// Custom form
	let customSystemPrompt = $state('You are an expert full-stack developer. Write clean, well-structured code with proper error handling, type safety, and following best practices.');
	let customUserPrompt = $state('');
	let customTemperature = $state(0.3);
	let customMaxTokens = $state(32000);

	// Scaffolding form
	let selectedStackSlug = $state('');
	let selectedAppIds = $state<Set<number>>(new Set());
	let selectedModelIds = $state<Set<number>>(new Set());
	let scaffoldingTemperature = $state(0.3);
	let scaffoldingMaxTokens = $state(32000);
	let scaffoldingTrials = $state(1);
	let appSearch = $state('');
	let categoryFilter = $state('');

	// Copilot form
	let copilotDescription = $state('');
	let copilotMaxIterations = $state(5);
	let copilotUseOpenSource = $state(true);

	// UI state (Advanced toggles)
	let customShowAdvanced = $state(false);
	let scaffoldingShowAdvanced = $state(false);
	let copilotShowAdvanced = $state(false);

	let lastDefaultedStackKey = $state<string>('');
	$effect(() => {
		const key = stacks.map(s => s.slug).join(',');
		if (key && key !== lastDefaultedStackKey && selectedStackSlug === '') {
			selectedStackSlug = stacks[0].slug;
			lastDefaultedStackKey = key;
		}
	});

	const appCategories = $derived(
		[...new Set(appTemplates.map(t => t.category).filter(Boolean))].sort() as string[]
	);

	const filteredAppTemplates = $derived(
		appTemplates.filter(t => {
			if (categoryFilter && t.category !== categoryFilter) return false;
			if (!appSearch) return true;
			const q = appSearch.toLowerCase();
			return t.name.toLowerCase().includes(q) || (t.description ?? '').toLowerCase().includes(q);
		})
	);

	function toggleAppTemplate(id: number) {
		const s = new Set(selectedAppIds);
		s.has(id) ? s.delete(id) : s.add(id);
		selectedAppIds = s;
	}

	function toggleModelSelection(id: number) {
		const s = new Set(selectedModelIds);
		s.has(id) ? s.delete(id) : s.add(id);
		selectedModelIds = s;
	}

	function selectAllApps() {
		selectedAppIds = new Set(filteredAppTemplates.map(t => t.id));
	}

	function clearApps() {
		selectedAppIds = new Set();
	}

	function selectAllModelIds() {
		selectedModelIds = new Set(models.map((m) => m.id));
	}

	function clearModelIds() {
		selectedModelIds = new Set();
	}

	function submitCustom() {
		if (!customModelId || !customUserPrompt.trim()) return;
		onSubmitCustom({
			model_id: customModelId as number,
			system_prompt: customSystemPrompt,
			user_prompt: customUserPrompt,
			temperature: customTemperature,
			max_tokens: customMaxTokens,
		});
	}

	function submitScaffolding() {
		if (!selectedStackSlug || selectedAppIds.size === 0 || selectedModelIds.size === 0) return;
		onSubmitScaffolding({
			stack_slug: selectedStackSlug,
			app_requirement_ids: [...selectedAppIds],
			model_ids: [...selectedModelIds],
			temperature: scaffoldingTemperature,
			max_tokens: scaffoldingMaxTokens,
			trials: scaffoldingTrials,
		});
	}

	function submitCopilot() {
		if (!copilotDescription.trim()) return;
		onSubmitCopilot({
			description: copilotDescription,
			model_id: copilotModelId ? (copilotModelId as number) : undefined,
			max_iterations: copilotMaxIterations,
			use_open_source: copilotUseOpenSource,
		});
	}
</script>

<!-- Tabs are rendered by the parent route -->

{#if activeTab === 'custom'}
	<Card.Root>
		<Card.Header class="pb-3">
			<Card.Title>Custom Generation</Card.Title>
			<Card.Description>Send a custom prompt to any model and get a generated response.</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="space-y-5">
				<div class="space-y-1.5">
					<Label class="text-xs uppercase tracking-wide text-muted-foreground" for="custom-system-prompt">System Prompt</Label>
					<Textarea
						id="custom-system-prompt"
						bind:value={customSystemPrompt}
						rows={3}
						placeholder="Set the AI's behavior and role..."
					/>
				</div>

				<div class="space-y-1.5">
					<Label class="text-xs uppercase tracking-wide text-muted-foreground" for="custom-user-prompt">User Prompt</Label>
					<Textarea
						id="custom-user-prompt"
						bind:value={customUserPrompt}
						rows={6}
						placeholder="Describe what you want the AI to generate..."
					/>
				</div>

				<Separator />

				<ModelSelector
					{models}
					loading={modelsLoading}
					mode="single"
					bind:selectedId={customModelId}
				/>

				<!-- Advanced (collapsed by default) -->
				<div class="space-y-2">
					<button
						type="button"
						class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
						onclick={() => (customShowAdvanced = !customShowAdvanced)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {customShowAdvanced ? 'rotate-90' : ''}" />
						Advanced
					</button>
					{#if customShowAdvanced}
						<div class="grid gap-4 sm:grid-cols-2 rounded-md border bg-muted/20 p-3">
							<div class="space-y-1.5">
								<Label class="text-xs">Temperature: {customTemperature.toFixed(1)}</Label>
								<input
									type="range" min="0" max="2" step="0.1"
									bind:value={customTemperature}
									class="w-full accent-primary"
								/>
								<div class="flex justify-between text-[10px] text-muted-foreground">
									<span>Precise</span><span>Creative</span>
								</div>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs" for="custom-max-tokens">Max Tokens</Label>
								<input
									id="custom-max-tokens"
									type="number"
									bind:value={customMaxTokens}
									min={1}
									max={200000}
									class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
								/>
							</div>
						</div>
					{/if}
				</div>

				{#if customError}
					<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
						{customError}
					</div>
				{/if}

				<!-- Action row -->
				<div class="flex items-center gap-3 rounded-md border bg-muted/20 px-3 py-2">
					<Button
						class="ml-auto"
						onclick={submitCustom}
						disabled={customSubmitting || !customModelId || !customUserPrompt.trim()}
						title={!customModelId ? 'Select a model first' : !customUserPrompt.trim() ? 'Enter a user prompt' : undefined}
					>
						{#if customSubmitting}
							<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Submitting…
						{:else}
							<Play class="mr-2 h-4 w-4" /> Generate
						{/if}
					</Button>
				</div>
			</div>
		</Card.Content>
	</Card.Root>
{/if}

{#if activeTab === 'scaffolding'}
	<Card.Root>
		<Card.Header class="pb-3">
			<Card.Title>Scaffolding Batch</Card.Title>
			<Card.Description>Select stack, apps, and models. Each job freezes a reproducible bundle snapshot.</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="space-y-5">

				<!-- Stack pills -->
				<div class="space-y-2">
					<Label class="text-xs uppercase tracking-wide text-muted-foreground">Stack</Label>
					{#if scaffoldingLoading}
						<div class="flex items-center gap-2 text-sm text-muted-foreground">
							<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading…
						</div>
					{:else if stacks.length === 0}
						<p class="text-sm text-muted-foreground">No stacks found.</p>
					{:else}
						<div class="flex flex-wrap gap-2">
							{#each stacks as stack}
								<button
									type="button"
									class="flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium transition-colors cursor-pointer
										{selectedStackSlug === stack.slug
											? 'border-primary bg-primary/10 text-primary'
											: 'border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground'}"
									onclick={() => (selectedStackSlug = stack.slug)}
								>
									{stack.slug}
									{#if !stack.has_frontend}
										<span class="text-[9px] opacity-60">backend-only</span>
									{/if}
								</button>
							{/each}
						</div>
					{/if}
				</div>

				<Separator />

				<!-- Apps + Models: min-w-0 on both columns prevents fr-fraction overflow from shrink-0 content -->
				<div class="grid gap-4 md:grid-cols-[5fr_6fr]">
					<!-- App list -->
					<div class="min-w-0 space-y-2">
						<div class="flex items-center justify-between">
							<Label class="text-xs uppercase tracking-wide text-muted-foreground">Apps</Label>
							<div class="flex items-center gap-2 text-[10px]">
								<button type="button" class="text-primary hover:underline cursor-pointer" onclick={selectAllApps}>All</button>
								<span class="text-muted-foreground">·</span>
								<button type="button" class="text-muted-foreground hover:text-foreground hover:underline cursor-pointer" onclick={clearApps}>None</button>
								{#if selectedAppIds.size > 0}
									<Badge variant="secondary" class="ml-1 text-[9px]">{selectedAppIds.size}</Badge>
								{/if}
							</div>
						</div>
						{#if appCategories.length > 1}
							<div class="flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
								<button
									type="button"
									class="shrink-0 rounded px-2 py-0.5 text-[10px] font-medium transition-colors cursor-pointer {categoryFilter === '' ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground hover:text-foreground'}"
									onclick={() => (categoryFilter = '')}
								>All</button>
								{#each appCategories as cat}
									<button
										type="button"
										class="shrink-0 rounded px-2 py-0.5 text-[10px] font-medium transition-colors cursor-pointer {categoryFilter === cat ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground hover:text-foreground'}"
										onclick={() => (categoryFilter = categoryFilter === cat ? '' : cat)}
									>{cat}</button>
								{/each}
							</div>
						{/if}
						<div class="relative">
							<Search class="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" />
							<Input bind:value={appSearch} placeholder="Filter apps…" class="h-7 pl-8 text-xs" />
						</div>
						<div class="max-h-60 overflow-y-auto rounded-md border divide-y divide-border/50">
							{#if scaffoldingLoading}
								<div class="flex items-center justify-center py-6 text-sm text-muted-foreground">
									<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading…
								</div>
							{:else}
								{#each filteredAppTemplates as app}
									<button
										type="button"
										class="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted/40 cursor-pointer {selectedAppIds.has(app.id) ? 'bg-primary/5' : ''}"
										onclick={() => toggleAppTemplate(app.id)}
									>
										<div class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition-colors {selectedAppIds.has(app.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/40'}">
											{#if selectedAppIds.has(app.id)}<Check class="h-2.5 w-2.5" />{/if}
										</div>
										<span class="font-medium">{app.name}</span>
										<Badge variant="secondary" class="ml-auto shrink-0 text-[9px]">{app.category}</Badge>
									</button>
								{/each}
								{#if filteredAppTemplates.length === 0}
									<p class="py-4 text-center text-xs text-muted-foreground">No apps found.</p>
								{/if}
							{/if}
						</div>
					</div>

					<!-- Model selector — min-w-0 keeps it inside the fr column -->
					<div class="min-w-0">
						<ModelSelector
							{models}
							loading={modelsLoading}
							mode="multi"
							bind:selectedIds={selectedModelIds}
							onSelectAll={selectAllModelIds}
							onClearAll={clearModelIds}
						/>
					</div>
				</div>

				<!-- Advanced (collapsed by default) -->
				<div class="space-y-2">
					<button
						type="button"
						class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
						onclick={() => (scaffoldingShowAdvanced = !scaffoldingShowAdvanced)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {scaffoldingShowAdvanced ? 'rotate-90' : ''}" />
						Advanced
					</button>
					{#if scaffoldingShowAdvanced}
						<div class="grid gap-4 sm:grid-cols-2 rounded-md border bg-muted/20 p-3">
							<div class="space-y-1.5">
								<Label class="text-xs">Temperature: {scaffoldingTemperature.toFixed(1)}</Label>
								<input
									type="range" min="0" max="2" step="0.1"
									bind:value={scaffoldingTemperature}
									class="w-full accent-primary"
								/>
								<div class="flex justify-between text-[10px] text-muted-foreground">
									<span>Precise</span><span>Creative</span>
								</div>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs" for="scaffolding-max-tokens">Max Tokens</Label>
								<input
									id="scaffolding-max-tokens"
									type="number"
									bind:value={scaffoldingMaxTokens}
									min={1}
									max={200000}
									class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
								/>
							</div>
							<div class="space-y-1.5">
								<Label class="text-xs" for="scaffolding-trials">Repeats per Combination</Label>
								<input
									id="scaffolding-trials"
									type="number"
									bind:value={scaffoldingTrials}
									min={1}
									max={10}
									class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
								/>
								<p class="text-[10px] text-muted-foreground">Independent trials per app × model — comparisons need n &gt; 1.</p>
							</div>
						</div>
					{/if}
				</div>

				<!-- Error / success feedback -->
				{#if scaffoldingError}
					<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
						{scaffoldingError}
					</div>
				{/if}
				{#if scaffoldingResult}
					<div class="rounded-md bg-emerald-500/10 border border-emerald-500/30 px-4 py-3 text-sm text-emerald-400">
						<div class="flex items-center gap-2">
							<Check class="h-4 w-4" />
							Batch created — {scaffoldingResult.job_count} jobs queued.
						</div>
						<div class="mt-1 text-xs text-muted-foreground">
							Batch <span class="font-mono">{scaffoldingResult.batch_id.slice(0, 8)}…</span>
						</div>
					</div>
				{/if}

				<!-- Action row — job tally always visible -->
				<div class="flex items-center gap-3 rounded-md border bg-muted/20 px-3 py-2">
					<div class="flex flex-wrap items-baseline gap-1 text-xs">
						<span class="tabular-nums {selectedAppIds.size > 0 ? 'text-foreground font-medium' : 'text-muted-foreground'}">{selectedAppIds.size}</span>
						<span class="text-muted-foreground">apps ×</span>
						<span class="tabular-nums {selectedModelIds.size > 0 ? 'text-foreground font-medium' : 'text-muted-foreground'}">{selectedModelIds.size}</span>
						<span class="text-muted-foreground">models</span>
						{#if scaffoldingTrials > 1}
							<span class="text-muted-foreground">×</span>
							<span class="tabular-nums text-foreground font-medium">{scaffoldingTrials}</span>
							<span class="text-muted-foreground">repeats</span>
						{/if}
						<span class="text-muted-foreground">=</span>
						<span class="tabular-nums text-base font-bold leading-none {selectedAppIds.size * selectedModelIds.size * scaffoldingTrials > 0 ? 'text-primary' : 'text-muted-foreground/50'}">{selectedAppIds.size * selectedModelIds.size * scaffoldingTrials}</span>
						<span class="text-muted-foreground">jobs</span>
					</div>
					<Button
						class="ml-auto"
						onclick={submitScaffolding}
						disabled={scaffoldingSubmitting || !selectedStackSlug || selectedAppIds.size === 0 || selectedModelIds.size === 0}
						title={selectedAppIds.size === 0 ? 'Select at least one app' : selectedModelIds.size === 0 ? 'Select at least one model' : undefined}
					>
						{#if scaffoldingSubmitting}
							<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Creating…
						{:else}
							<Play class="mr-2 h-4 w-4" /> Generate Batch
						{/if}
					</Button>
				</div>

			</div>
		</Card.Content>
	</Card.Root>
{/if}

{#if activeTab === 'copilot'}
	<Card.Root>
		<Card.Header class="pb-3">
			<div class="flex flex-wrap items-center gap-2">
				<Card.Title>Copilot Generation</Card.Title>
				<Badge variant="secondary" class="text-[10px]">Powered by Aider</Badge>
			</div>
			<Card.Description>
				Describe your app. Aider edits a scaffolded git workspace using your stored OpenRouter key,
				iterating until validation passes.
			</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="space-y-5">
				<div class="space-y-1.5">
					<Label class="text-xs uppercase tracking-wide text-muted-foreground" for="copilot-description">Description</Label>
					<Textarea
						id="copilot-description"
						bind:value={copilotDescription}
						rows={5}
						placeholder="Describe what you want to build. Be specific about features, tech stack, and requirements…"
					/>
				</div>

				<Separator />

				<div class="space-y-1.5">
					<ModelSelector
						{models}
						loading={modelsLoading}
						mode="single"
						allowEmpty
						emptyLabel="Auto-select (DeepSeek or GPT-4o mini)"
						bind:selectedId={copilotModelId}
					/>
					<p class="text-[10px] text-muted-foreground">
						{#if copilotModelId === ''}
							When auto-select is on, "Use open-source models" picks DeepSeek vs GPT-4o mini.
						{:else}
							Selected model is used with your OpenRouter key from Settings.
						{/if}
					</p>
				</div>

				<!-- Advanced (collapsed by default) -->
				<div class="space-y-2">
					<button
						type="button"
						class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
						onclick={() => (copilotShowAdvanced = !copilotShowAdvanced)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {copilotShowAdvanced ? 'rotate-90' : ''}" />
						Advanced
					</button>
					{#if copilotShowAdvanced}
						<div class="grid gap-4 sm:grid-cols-2 rounded-md border bg-muted/20 p-3">
							<div class="space-y-1.5">
								<Label class="text-xs">Max Iterations: {copilotMaxIterations}</Label>
								<input
									type="range" min="1" max="10" step="1"
									bind:value={copilotMaxIterations}
									class="w-full accent-primary"
								/>
								<div class="flex justify-between text-[10px] text-muted-foreground">
									<span>1</span><span>10</span>
								</div>
							</div>
							<div class="space-y-1.5 flex flex-col justify-center">
								<Switch
									bind:checked={copilotUseOpenSource}
									disabled={copilotModelId !== ''}
									label="Prefer open-source models (auto-select only)"
									id="copilot-open-source"
								/>
							</div>
						</div>
					{/if}
				</div>

				{#if copilotError}
					<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
						{copilotError}
					</div>
				{/if}

				<!-- Action row -->
				<div class="flex items-center gap-3 rounded-md border bg-muted/20 px-3 py-2">
					<Button
						class="ml-auto"
						onclick={submitCopilot}
						disabled={copilotSubmitting || !copilotDescription.trim()}
						title={!copilotDescription.trim() ? 'Enter a description first' : undefined}
					>
						{#if copilotSubmitting}
							<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Starting Aider…
						{:else}
							<Bot class="mr-2 h-4 w-4" /> Start Aider Copilot
						{/if}
					</Button>
				</div>
			</div>
		</Card.Content>
	</Card.Root>
{/if}
