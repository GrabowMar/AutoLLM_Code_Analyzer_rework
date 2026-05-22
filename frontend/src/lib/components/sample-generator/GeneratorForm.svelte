<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Separator } from '$lib/components/ui/separator';
	import type {
		LLMModelSummary,
		ScaffoldingTemplate,
		AppRequirementTemplate,
	} from '$lib/api/client';
	import Play from '@lucide/svelte/icons/play';
	import Search from '@lucide/svelte/icons/search';
	import Check from '@lucide/svelte/icons/check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Layers from '@lucide/svelte/icons/layers';
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
		scaffolding_template_id: number;
		app_requirement_ids: number[];
		model_ids: number[];
		temperature: number;
		max_tokens: number;
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
		scaffoldingTemplates: ScaffoldingTemplate[];
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
		scaffoldingTemplates,
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
	let selectedScaffoldId = $state<number | ''>('');
	let selectedAppIds = $state<Set<number>>(new Set());
	let selectedModelIds = $state<Set<number>>(new Set());
	let scaffoldingTemperature = $state(0.3);
	let scaffoldingMaxTokens = $state(32000);
	let appSearch = $state('');

	// Copilot form
	let copilotDescription = $state('');
	let copilotMaxIterations = $state(5);
	let copilotUseOpenSource = $state(true);

	let lastDefaultedScaffoldKey = $state<string>('');
	$effect(() => {
		const key = scaffoldingTemplates.map(s => s.id).join(',');
		if (key && key !== lastDefaultedScaffoldKey && selectedScaffoldId === '') {
			const def = scaffoldingTemplates.find(s => s.is_default);
			selectedScaffoldId = def ? def.id : scaffoldingTemplates[0].id;
			lastDefaultedScaffoldKey = key;
		}
	});

	const filteredAppTemplates = $derived(
		appTemplates.filter(t =>
			t.name.toLowerCase().includes(appSearch.toLowerCase()) ||
			t.description.toLowerCase().includes(appSearch.toLowerCase())
		)
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
		if (!selectedScaffoldId || selectedAppIds.size === 0 || selectedModelIds.size === 0) return;
		onSubmitScaffolding({
			scaffolding_template_id: selectedScaffoldId as number,
			app_requirement_ids: [...selectedAppIds],
			model_ids: [...selectedModelIds],
			temperature: scaffoldingTemperature,
			max_tokens: scaffoldingMaxTokens,
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
		<Card.Header>
			<Card.Title>Custom Generation</Card.Title>
			<Card.Description>Send a custom prompt to any model and get a generated response.</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="space-y-4">
				<div class="space-y-2">
					<Label>System Prompt</Label>
					<textarea
						bind:value={customSystemPrompt}
						rows={3}
						class="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
						placeholder="Set the AI's behavior and role..."
					></textarea>
				</div>

				<div class="space-y-2">
					<Label>User Prompt</Label>
					<textarea
						bind:value={customUserPrompt}
						rows={6}
						class="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
						placeholder="Describe what you want the AI to generate..."
					></textarea>
				</div>

				<ModelSelector
					{models}
					loading={modelsLoading}
					mode="single"
					bind:selectedId={customModelId}
				/>

				<div class="grid gap-4 grid-cols-1 sm:grid-cols-2">
					<div class="space-y-2">
						<Label>Temperature: {customTemperature.toFixed(1)}</Label>
						<input
							type="range"
							min="0"
							max="2"
							step="0.1"
							bind:value={customTemperature}
							class="w-full accent-primary"
						/>
						<div class="flex justify-between text-[10px] text-muted-foreground">
							<span>Precise</span>
							<span>Creative</span>
						</div>
					</div>
					<div class="space-y-2">
						<Label>Max Tokens</Label>
						<input
							type="number"
							bind:value={customMaxTokens}
							min={1}
							max={200000}
							class="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
						/>
					</div>
				</div>

				{#if customError}
					<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
						{customError}
					</div>
				{/if}

				<Button
					onclick={submitCustom}
					disabled={customSubmitting || !customModelId || !customUserPrompt.trim()}
				>
					{#if customSubmitting}
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Submitting…
					{:else}
						<Play class="mr-2 h-4 w-4" /> Generate
					{/if}
				</Button>
			</div>
		</Card.Content>
	</Card.Root>
{/if}

{#if activeTab === 'scaffolding'}
	<Card.Root>
		<Card.Header>
			<Card.Title>Scaffolding Batch Generation</Card.Title>
			<Card.Description>Select a scaffolding template, app requirements, and models to generate in batch.</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="space-y-6">
				<!-- Scaffolding Template -->
				<div class="space-y-2">
					<Label>Scaffolding Template</Label>
					{#if scaffoldingLoading}
						<div class="flex h-9 items-center gap-2 rounded-md border px-3 text-sm text-muted-foreground">
							<LoaderCircle class="h-3.5 w-3.5 animate-spin" /> Loading templates…
						</div>
					{:else}
						<div class="grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3">
							{#each scaffoldingTemplates as tpl}
								<button
									class="rounded-lg border p-4 text-left transition-colors {selectedScaffoldId === tpl.id ? 'ring-2 ring-primary bg-primary/5' : 'hover:bg-muted/50'}"
									onclick={() => selectedScaffoldId = tpl.id}
								>
									<div class="flex items-center gap-2">
										<Layers class="h-4 w-4 {selectedScaffoldId === tpl.id ? 'text-primary' : 'text-muted-foreground'}" />
										<span class="text-sm font-medium">{tpl.name}</span>
										{#if tpl.is_default}
											<Badge variant="secondary" class="text-[10px]">Default</Badge>
										{/if}
									</div>
									<p class="mt-1 text-xs text-muted-foreground line-clamp-2">{tpl.description}</p>
									{#if Object.keys(tpl.tech_stack).length > 0}
										<div class="mt-2 flex flex-wrap gap-1">
											{#each Object.entries(tpl.tech_stack) as [key, val]}
												<Badge variant="outline" class="text-[10px]">{key}: {val}</Badge>
											{/each}
										</div>
									{/if}
								</button>
							{/each}
						</div>
						{#if scaffoldingTemplates.length === 0}
							<p class="text-sm text-muted-foreground">No scaffolding templates found. Create one in the admin.</p>
						{/if}
					{/if}
				</div>

				<Separator />

				<!-- App Requirements + Models side by side -->
				<div class="grid gap-4 md:grid-cols-2">
					<!-- App Requirements -->
					<div class="space-y-3">
						<div class="flex items-center justify-between">
							<Label>App Requirements</Label>
							<div class="flex gap-1 text-[10px]">
								<button class="text-primary hover:underline" onclick={selectAllApps}>Select All</button>
								<span class="text-muted-foreground">|</span>
								<button class="text-muted-foreground hover:underline" onclick={clearApps}>Clear</button>
								<span class="ml-1 text-muted-foreground">({selectedAppIds.size})</span>
							</div>
						</div>
						<div class="relative">
							<Search class="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-muted-foreground" />
							<Input bind:value={appSearch} placeholder="Search apps…" class="h-8 pl-8 text-xs" />
						</div>
						<div class="max-h-64 space-y-1 overflow-y-auto rounded-md border p-1">
							{#if scaffoldingLoading}
								<div class="flex items-center justify-center py-6 text-sm text-muted-foreground">
									<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Loading…
								</div>
							{:else}
								{#each filteredAppTemplates as app}
									<button
										class="flex w-full items-start gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors hover:bg-muted/50 {selectedAppIds.has(app.id) ? 'bg-primary/5 ring-1 ring-primary/20' : ''}"
										onclick={() => toggleAppTemplate(app.id)}
									>
										<div class="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border {selectedAppIds.has(app.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-border'}">
											{#if selectedAppIds.has(app.id)}<Check class="h-3 w-3" />{/if}
										</div>
										<div class="min-w-0">
											<div class="font-medium">{app.name}</div>
											<div class="text-xs text-muted-foreground line-clamp-1">{app.description}</div>
										</div>
										<Badge variant="secondary" class="ml-auto shrink-0 text-[10px]">{app.category}</Badge>
									</button>
								{/each}
								{#if filteredAppTemplates.length === 0}
									<p class="py-4 text-center text-xs text-muted-foreground">No templates found.</p>
								{/if}
							{/if}
						</div>
					</div>

					<ModelSelector
						{models}
						loading={modelsLoading}
						mode="multi"
						bind:selectedIds={selectedModelIds}
						onSelectAll={selectAllModelIds}
						onClearAll={clearModelIds}
					/>
				</div>

				<Separator />

				<!-- Controls row -->
				<div class="flex flex-col gap-4 sm:flex-row sm:flex-wrap sm:items-end">
					<div class="space-y-2">
						<Label>Temperature: {scaffoldingTemperature.toFixed(1)}</Label>
						<input
							type="range"
							min="0"
							max="2"
							step="0.1"
							bind:value={scaffoldingTemperature}
							class="w-40 accent-primary"
						/>
					</div>
					<div class="space-y-2">
						<Label>Max Tokens</Label>
						<input
							type="number"
							bind:value={scaffoldingMaxTokens}
							min={1}
							max={200000}
							class="flex h-9 w-32 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
						/>
					</div>
					<div class="ml-auto flex items-center gap-3">
						{#if selectedAppIds.size > 0 && selectedModelIds.size > 0}
							<span class="text-sm text-muted-foreground">
								{selectedAppIds.size} apps × {selectedModelIds.size} models = <span class="font-bold text-primary">{selectedAppIds.size * selectedModelIds.size}</span> jobs
							</span>
						{/if}
						<Button
							onclick={submitScaffolding}
							disabled={scaffoldingSubmitting || !selectedScaffoldId || selectedAppIds.size === 0 || selectedModelIds.size === 0}
						>
							{#if scaffoldingSubmitting}
								<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Creating…
							{:else}
								<Play class="mr-2 h-4 w-4" /> Generate Batch
							{/if}
						</Button>
					</div>
				</div>

				{#if scaffoldingError}
					<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
						{scaffoldingError}
					</div>
				{/if}

				{#if scaffoldingResult}
					<div class="rounded-md bg-emerald-500/10 border border-emerald-500/30 px-4 py-3 text-sm text-emerald-400">
						<div class="flex items-center gap-2">
							<Check class="h-4 w-4" />
							Batch created with {scaffoldingResult.job_count} jobs.
						</div>
						<div class="mt-1 text-xs text-muted-foreground">
							Batch ID: <span class="font-mono">{scaffoldingResult.batch_id}</span> · Status: {scaffoldingResult.status}
						</div>
					</div>
				{/if}
			</div>
		</Card.Content>
	</Card.Root>
{/if}

{#if activeTab === 'copilot'}
	<Card.Root>
		<Card.Header>
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
			<div class="space-y-4">
				<div class="space-y-2">
					<Label>Description</Label>
					<textarea
						bind:value={copilotDescription}
						rows={5}
						class="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
						placeholder="Describe what you want to build. Be specific about features, tech stack, and requirements…"
					></textarea>
				</div>

				<div class="space-y-1">
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
							When auto-select is on, “Use open-source models” picks DeepSeek vs GPT-4o mini.
						{:else}
							Selected model is used with your OpenRouter key from Settings.
						{/if}
					</p>
				</div>

				<div class="space-y-2">
					<Label>Max Iterations: {copilotMaxIterations}</Label>
					<input
						type="range"
						min="1"
						max="10"
						step="1"
						bind:value={copilotMaxIterations}
						class="w-full accent-primary"
					/>
					<div class="flex justify-between text-[10px] text-muted-foreground">
						<span>1</span>
						<span>10</span>
					</div>
				</div>

				<label
					class="flex items-center gap-2 text-sm cursor-pointer {copilotModelId !== '' ? 'opacity-50' : ''}"
					title={copilotModelId !== '' ? 'Only applies when model is auto-select' : ''}
				>
					<div class="relative">
						<input
							type="checkbox"
							bind:checked={copilotUseOpenSource}
							disabled={copilotModelId !== ''}
							class="peer sr-only"
						/>
						<div class="h-5 w-9 rounded-full bg-muted transition-colors peer-checked:bg-primary peer-disabled:opacity-50"></div>
						<div
							class="absolute left-0.5 top-0.5 h-4 w-4 rounded-full bg-background shadow-sm transition-transform peer-checked:translate-x-4"
						></div>
					</div>
					Prefer open-source models (auto-select only)
				</label>

				{#if copilotError}
					<div class="rounded-md bg-red-500/10 border border-red-500/30 px-4 py-3 text-sm text-red-400">
						{copilotError}
					</div>
				{/if}

				<Button onclick={submitCopilot} disabled={copilotSubmitting || !copilotDescription.trim()}>
					{#if copilotSubmitting}
						<LoaderCircle class="mr-2 h-4 w-4 animate-spin" /> Starting Aider…
					{:else}
						<Bot class="mr-2 h-4 w-4" /> Start Aider Copilot
					{/if}
				</Button>
			</div>
		</Card.Content>
	</Card.Root>
{/if}
