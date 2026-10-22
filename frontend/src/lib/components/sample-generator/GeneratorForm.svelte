<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Textarea } from '$lib/components/ui/textarea';
	import { Label } from '$lib/components/ui/label';
	import { Separator } from '$lib/components/ui/separator';
	import * as Dialog from '$lib/components/ui/dialog';
	import {
		getProfilePreview,
		suggestProfiles,
		type AgentConfig,
		type AgentHarness,
		type AppRequirementTemplate,
		type GenerationProfile,
		type LLMModelSummary,
		type LLMParams,
		type ProfilePreview,
		type ProfileSuggestion,
		type Stack,
	} from '$lib/api/client';
	import Play from '@lucide/svelte/icons/play';
	import Search from '@lucide/svelte/icons/search';
	import Check from '@lucide/svelte/icons/check';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Bot from '@lucide/svelte/icons/bot';
	import ModelSelector from '$lib/components/sample-generator/ModelSelector.svelte';
	import LLMParamsEditor from '$lib/components/sample-generator/LLMParamsEditor.svelte';
	import ProfilePicker from '$lib/components/sample-generator/ProfilePicker.svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { toneClasses } from '$lib/constants/colors';

	type TabId = 'agent' | 'custom' | 'scaffolding';

	export interface CustomPayload {
		model_id: number;
		system_prompt: string;
		user_prompt: string;
		llm_params: LLMParams;
		seed?: number | null;
	}

	export interface ScaffoldingPayload {
		stack_slug: string;
		app_requirement_ids: number[];
		model_ids: number[];
		llm_params: LLMParams;
		profile_id?: number | null;
		trials: number;
	}

	export interface AgentPayload {
		stack_slug: string;
		app_requirement_ids: number[];
		description: string;
		model_ids: number[];
		harnesses: string[];
		llm_params: LLMParams;
		agent_config: AgentConfig;
		trials: number;
		base_seed: number | null;
	}

	interface Props {
		activeTab: TabId;
		models: LLMModelSummary[];
		harnesses?: AgentHarness[];
		modelsLoading: boolean;
		stacks: Stack[];
		appTemplates: AppRequirementTemplate[];
		profiles: GenerationProfile[];
		scaffoldingLoading: boolean;
		customSubmitting: boolean;
		customError: string;
		scaffoldingSubmitting: boolean;
		scaffoldingError: string;
		scaffoldingResult: { batch_id: string; job_count: number; status: string } | null;
		agentSubmitting: boolean;
		agentError: string;
		agentResult: { batch_id: string; job_count: number; status: string } | null;
		customModelId?: number | '';
		onSubmitCustom: (payload: CustomPayload) => void;
		onSubmitScaffolding: (payload: ScaffoldingPayload) => void;
		onSubmitAgent: (payload: AgentPayload) => void;
	}

	let {
		activeTab,
		models,
		harnesses = [],
		modelsLoading,
		stacks,
		appTemplates,
		profiles,
		scaffoldingLoading,
		customSubmitting,
		customError,
		scaffoldingSubmitting,
		scaffoldingError,
		scaffoldingResult,
		agentSubmitting,
		agentError,
		agentResult,
		customModelId = $bindable('' as number | ''),
		onSubmitCustom,
		onSubmitScaffolding,
		onSubmitAgent,
	}: Props = $props();

	// Custom form
	let customSystemPrompt = $state('You are an expert full-stack developer. Write clean, well-structured code with proper error handling, type safety, and following best practices.');
	let customUserPrompt = $state('');
	let customLLMParams = $state<LLMParams>({});
	let customSeed = $state<number | ''>('');

	// Scaffolding form
	let selectedStackSlug = $state('');
	let selectedAppIds = $state<Set<number>>(new Set());
	let selectedModelIds = $state<Set<number>>(new Set());
	let scaffoldingLLMParams = $state<LLMParams>({});
	let scaffoldingTrials = $state(1);
	let selectedProfileId = $state<number | ''>('');
	let suggestions = $state<ProfileSuggestion[]>([]);
	let suggestTimer: ReturnType<typeof setTimeout> | undefined;

	const selectedProfile = $derived(profiles.find((p) => p.id === selectedProfileId));

	// The profile owns the stack: server-side, the profile's scaffolding_slug
	// wins over the picked stack, so reflect that instead of hiding it.
	function onProfileSelect(profile: GenerationProfile | null) {
		if (profile) selectedStackSlug = profile.scaffolding_slug;
	}

	$effect(() => {
		const ids = [...selectedAppIds];
		const stack = selectedStackSlug;
		clearTimeout(suggestTimer);
		if (ids.length === 0) {
			suggestions = [];
			return;
		}
		suggestTimer = setTimeout(async () => {
			try {
				suggestions = await suggestProfiles(ids, stack || undefined);
			} catch {
				suggestions = [];
			}
		}, 300);
	});

	// Prompt preview dialog (single selected app)
	let promptPreviewOpen = $state(false);
	let promptPreview = $state<ProfilePreview | null>(null);
	let promptPreviewLoading = $state(false);
	let promptPreviewError = $state('');
	let promptPreviewStage = $state('backend');

	const previewableSlug = $derived.by(() => {
		if (selectedProfile) return selectedProfile.slug;
		if (suggestions.length === 1) return suggestions[0].slug;
		return null;
	});
	const previewAppSlug = $derived.by(() => {
		if (selectedAppIds.size !== 1) return null;
		const id = [...selectedAppIds][0];
		return appTemplates.find((t) => t.id === id)?.slug ?? null;
	});

	async function openPromptPreview() {
		if (!previewableSlug || !previewAppSlug) return;
		promptPreviewOpen = true;
		promptPreviewLoading = true;
		promptPreviewError = '';
		promptPreview = null;
		try {
			promptPreview = await getProfilePreview(previewableSlug, previewAppSlug);
		} catch (e: any) {
			promptPreviewError = e?.detail ?? e?.message ?? 'Preview failed';
		}
		promptPreviewLoading = false;
	}
	let appSearch = $state('');
	let categoryFilter = $state('');

	// Agent form (stack/app/model selection is shared with the scaffolding tab)
	let agentDescription = $state('');
	let agentLLMParams = $state<LLMParams>({});
	let agentTrials = $state(1);
	let agentBaseSeed = $state<number | ''>('');
	// Budget fields: '' = server default (AGENT_* settings).
	let agentWallMinutes = $state<number | ''>('');
	let agentMaxRequests = $state<number | ''>('');
	let agentMaxCost = $state<number | ''>('');
	let agentNetworkPolicy = $state<'' | 'packages' | 'none'>('');

	let agentHarnesses = $state<Set<string>>(new Set(['opencode']));
	let agentSamplingPolicy = $state<'' | 'provider_default' | 'harness_native'>('');

	// Tool-calling harnesses can only drive tool-calling models; if any selected
	// harness needs tools, every selected model must have them.
	const needsTools = $derived(
		[...agentHarnesses].some((name) => harnesses.find((h) => h.name === name)?.requires_tool_calling ?? true),
	);
	const agentModels = $derived(needsTools ? models.filter((m) => m.supports_function_calling) : models);
	const agentModelIds = $derived(new Set([...selectedModelIds].filter((id) => agentModels.some((m) => m.id === id))));
	const agentJobCount = $derived(
		Math.max(selectedAppIds.size, agentDescription.trim() ? 1 : 0) *
			agentModelIds.size *
			Math.max(agentHarnesses.size, 1) *
			agentTrials,
	);

	function toggleHarness(name: string) {
		const next = new Set(agentHarnesses);
		if (next.has(name)) {
			if (next.size > 1) next.delete(name);
		} else {
			next.add(name);
		}
		agentHarnesses = next;
	}

	// UI state (Advanced toggles)
	let customShowAdvanced = $state(false);
	let scaffoldingShowAdvanced = $state(false);
	let agentShowAdvanced = $state(false);

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
		selectedModelIds = new Set((activeTab === 'agent' ? agentModels : models).map((m) => m.id));
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
			llm_params: customLLMParams,
			seed: customSeed === '' ? null : customSeed,
		});
	}

	function submitScaffolding() {
		if (!selectedStackSlug || selectedAppIds.size === 0 || selectedModelIds.size === 0) return;
		onSubmitScaffolding({
			stack_slug: selectedStackSlug,
			app_requirement_ids: [...selectedAppIds],
			model_ids: [...selectedModelIds],
			llm_params: scaffoldingLLMParams,
			profile_id: selectedProfileId === '' ? null : selectedProfileId,
			trials: scaffoldingTrials,
		});
	}

	function submitAgent() {
		if (!selectedStackSlug || agentJobCount === 0) return;
		const agent_config: AgentConfig = {};
		if (agentWallMinutes !== '') agent_config.wall_clock_seconds = Math.round(agentWallMinutes * 60);
		if (agentMaxRequests !== '') agent_config.max_llm_requests = agentMaxRequests;
		if (agentMaxCost !== '') agent_config.max_cost_usd = agentMaxCost;
		if (agentNetworkPolicy) agent_config.network_policy = agentNetworkPolicy;
		if (agentSamplingPolicy) agent_config.sampling_policy = agentSamplingPolicy;
		onSubmitAgent({
			stack_slug: selectedStackSlug,
			app_requirement_ids: [...selectedAppIds],
			description: agentDescription,
			model_ids: [...agentModelIds],
			harnesses: [...agentHarnesses],
			llm_params: agentLLMParams,
			agent_config,
			trials: agentTrials,
			base_seed: agentBaseSeed === '' ? null : agentBaseSeed,
		});
	}

	const inputClass =
		'flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30';
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
					<Label class="text-xs" for="custom-system-prompt">System Prompt</Label>
					<Textarea
						id="custom-system-prompt"
						bind:value={customSystemPrompt}
						rows={3}
						placeholder="Set the AI's behavior and role..."
					/>
				</div>

				<div class="space-y-1.5">
					<Label class="text-xs" for="custom-user-prompt">User Prompt</Label>
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
						aria-expanded={customShowAdvanced}
						onclick={() => (customShowAdvanced = !customShowAdvanced)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {customShowAdvanced ? 'rotate-90' : ''}" />
						Advanced
					</button>
					{#if customShowAdvanced}
						<div class="space-y-3">
							<LLMParamsEditor bind:params={customLLMParams} idPrefix="custom" />
							<div class="grid gap-3 sm:grid-cols-2 rounded-md border bg-muted/20 p-3">
								<div class="space-y-1.5">
									<Label class="text-xs" for="custom-seed">Seed (optional)</Label>
									<input
										id="custom-seed"
										type="number"
										bind:value={customSeed}
										min={0}
										placeholder="random"
										class="flex h-9 w-full rounded-md border border-input bg-surface-1 px-3 py-1 text-sm shadow-xs transition-all hover:border-primary/40 focus-visible:outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30"
									/>
									<p class="text-[11px] text-muted-foreground">Fixed sampling seed for best-effort reproducibility.</p>
								</div>
							</div>
						</div>
					{/if}
				</div>

				{#if customError}
					<ErrorAlert message={customError} />
				{/if}

				<!-- Action row -->
				<div class="flex items-center gap-3 border-t pt-4">
					<Button
						class="ml-auto"
						onclick={submitCustom}
						disabled={customSubmitting || !customModelId || !customUserPrompt.trim()}
						title={!customModelId ? 'Select a model first' : !customUserPrompt.trim() ? 'Enter a user prompt' : undefined}
					>
						{#if customSubmitting}
							<LoaderCircle class="animate-spin" /> Submitting…
						{:else}
							<Play /> Generate
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

				{@render stackPicker()}

				<!-- Profile -->
				<div class="space-y-2">
					<div class="flex items-center justify-between">
						<Label class="text-xs">Profile</Label>
						{#if previewableSlug && previewAppSlug}
							<button
								type="button"
								class="text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
								onclick={openPromptPreview}
							>
								Preview prompts
							</button>
						{/if}
					</div>
					<ProfilePicker
						{profiles}
						loading={scaffoldingLoading}
						bind:selectedId={selectedProfileId}
						{suggestions}
						onSelect={onProfileSelect}
					/>
					{#if selectedProfile && selectedProfile.scaffolding_slug !== selectedStackSlug}
						<p class="text-[11px] text-warning-fg">The selected profile targets {selectedProfile.scaffolding_slug} — jobs will run on that stack.</p>
					{/if}
				</div>

				<Separator />

				<!-- Apps + Models: min-w-0 on both columns prevents fr-fraction overflow from shrink-0 content -->
				<div class="grid gap-4 md:grid-cols-[5fr_6fr]">
					{@render appPicker()}
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
						aria-expanded={scaffoldingShowAdvanced}
						onclick={() => (scaffoldingShowAdvanced = !scaffoldingShowAdvanced)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {scaffoldingShowAdvanced ? 'rotate-90' : ''}" />
						Advanced
					</button>
					{#if scaffoldingShowAdvanced}
						<div class="space-y-3">
							<LLMParamsEditor bind:params={scaffoldingLLMParams} idPrefix="scaffolding" />
							<div class="grid gap-3 sm:grid-cols-2 rounded-md border bg-muted/20 p-3">
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
									<p class="text-[11px] text-muted-foreground">Independent trials per app × model — comparisons need n &gt; 1.</p>
								</div>
							</div>
						</div>
					{/if}
				</div>

				<!-- Error / success feedback -->
				{#if scaffoldingError}
					<ErrorAlert message={scaffoldingError} />
				{/if}
				{#if scaffoldingResult}
					<div class="rounded-md border px-4 py-3 text-sm {toneClasses.success}" role="status">
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
				<div class="flex items-center gap-3 border-t pt-4">
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
							<LoaderCircle class="animate-spin" /> Creating…
						{:else}
							<Play /> Generate Batch
						{/if}
					</Button>
				</div>

			</div>
		</Card.Content>
	</Card.Root>
{/if}

{#if activeTab === 'agent'}
	<Card.Root>
		<Card.Header class="pb-3">
			<div class="flex flex-wrap items-center gap-2">
				<Card.Title>Agent Generation</Card.Title>
				<Badge variant="outline">sandboxed harnesses</Badge>
			</div>
			<Card.Description>
				A coding agent builds each app inside an isolated sandbox, starting from the stack skeleton. Every (harness, model)
				pair gets the same brief, budget, sandbox and seed per cell; every LLM call is recorded by the run's gateway, and
				the platform builds and grades every result with a hidden conformance suite.
			</Card.Description>
		</Card.Header>
		<Card.Content>
			<div class="space-y-5">
				{@render stackPicker()}

				<div class="space-y-2">
					<Label class="text-xs">Harnesses</Label>
					<div class="flex flex-wrap gap-2">
						{#each harnesses as h (h.name)}
							<button
								type="button"
								class="flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium transition-colors cursor-pointer
									{agentHarnesses.has(h.name)
										? 'border-primary bg-primary/10 text-primary'
										: 'border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground'}"
								aria-pressed={agentHarnesses.has(h.name)}
								onclick={() => toggleHarness(h.name)}
							>
								{h.label}
								<span class="font-mono text-[11px] opacity-70">{h.version}</span>
							</button>
						{/each}
					</div>
					<p class="text-[11px] text-muted-foreground">
						Each selected harness runs every task × model cell. Harnesses keep their native prompts, frozen; within a harness
						every model is treated identically.
					</p>
				</div>

				<div class="space-y-1.5">
					<Label class="text-xs" for="agent-description">Task description (optional)</Label>
					<Textarea
						id="agent-description"
						bind:value={agentDescription}
						rows={3}
						placeholder="Free-form task when no app spec is selected; with app specs it is added to every brief as extra context."
					/>
				</div>

				<Separator />

				<div class="grid gap-4 md:grid-cols-[5fr_6fr]">
					{@render appPicker()}
					<div class="min-w-0 space-y-1.5">
						<ModelSelector
							models={agentModels}
							loading={modelsLoading}
							mode="multi"
							bind:selectedIds={selectedModelIds}
							onSelectAll={selectAllModelIds}
							onClearAll={clearModelIds}
						/>
						<p class="text-[11px] text-muted-foreground">
							{needsTools ? 'A selected harness drives the model through tool calls, so only tool-calling models are listed.' : 'Aider needs no tool calling; every model is listed.'}
						</p>
					</div>
				</div>

				<div class="space-y-2">
					<button
						type="button"
						class="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
						aria-expanded={agentShowAdvanced}
						onclick={() => (agentShowAdvanced = !agentShowAdvanced)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {agentShowAdvanced ? 'rotate-90' : ''}" />
						Budget, repeats &amp; sampling
					</button>
					{#if agentShowAdvanced}
						<div class="space-y-3">
							<div class="grid gap-3 sm:grid-cols-3 rounded-md border bg-muted/20 p-3">
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-wall">Wall clock (minutes)</Label>
									<input id="agent-wall" type="number" min={1} max={240} bind:value={agentWallMinutes} placeholder="default 60" class={inputClass} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-requests">Max LLM requests</Label>
									<input id="agent-requests" type="number" min={1} max={2000} bind:value={agentMaxRequests} placeholder="default 100" class={inputClass} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-cost">Max cost (USD)</Label>
									<input id="agent-cost" type="number" min={0.001} step={0.05} bind:value={agentMaxCost} placeholder="default 5.00" class={inputClass} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-network">Network</Label>
									<select id="agent-network" bind:value={agentNetworkPolicy} class={inputClass}>
										<option value="">Default (package registries)</option>
										<option value="packages">Package registries only</option>
										<option value="none">None</option>
									</select>
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-sampling">Sampling</Label>
									<select id="agent-sampling" bind:value={agentSamplingPolicy} class={inputClass}>
										<option value="">Default (provider defaults)</option>
										<option value="provider_default">Provider defaults</option>
										<option value="harness_native">Harness native</option>
									</select>
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-trials">Repeats per combination</Label>
									<input id="agent-trials" type="number" min={1} max={10} bind:value={agentTrials} class={inputClass} />
								</div>
								<div class="space-y-1.5">
									<Label class="text-xs" for="agent-seed">Base seed (optional)</Label>
									<input id="agent-seed" type="number" min={0} bind:value={agentBaseSeed} placeholder="random" class={inputClass} />
								</div>
								<p class="sm:col-span-3 text-[11px] text-muted-foreground">
									The request budget binds; wall clock and cost are safety nets (a cost cap gives expensive models fewer steps).
									Runs that hit a limit still count as samples with their termination recorded. Comparisons need n &gt; 1 repeats;
									every condition of a cell shares one seed, and a base seed makes the whole seed matrix reproducible. "Provider
									defaults" strips the sampling settings harnesses would send themselves, so only the params set below reach the model.
								</p>
							</div>
							<LLMParamsEditor bind:params={agentLLMParams} idPrefix="agent" />
						</div>
					{/if}
				</div>

				{#if agentError}
					<ErrorAlert message={agentError} />
				{/if}
				{#if agentResult}
					<div class="rounded-md border px-4 py-3 text-sm {toneClasses.success}" role="status">
						<div class="flex items-center gap-2">
							<Check class="h-4 w-4" />
							Batch created — {agentResult.job_count} agent runs queued.
						</div>
						<div class="mt-1 text-xs text-muted-foreground">
							Batch <span class="font-mono">{agentResult.batch_id.slice(0, 8)}…</span> · runs execute a few at a time; follow them in the history below.
						</div>
					</div>
				{/if}

				<div class="flex items-center gap-3 border-t pt-4">
					<div class="flex flex-wrap items-baseline gap-1 text-xs">
						<span class="tabular-nums text-foreground font-medium">{Math.max(selectedAppIds.size, agentDescription.trim() ? 1 : 0)}</span>
						<span class="text-muted-foreground">tasks ×</span>
						<span class="tabular-nums text-foreground font-medium">{agentModelIds.size}</span>
						<span class="text-muted-foreground">models ×</span>
						<span class="tabular-nums text-foreground font-medium">{agentHarnesses.size}</span>
						<span class="text-muted-foreground">harnesses</span>
						{#if agentTrials > 1}
							<span class="text-muted-foreground">×</span>
							<span class="tabular-nums text-foreground font-medium">{agentTrials}</span>
							<span class="text-muted-foreground">repeats</span>
						{/if}
						<span class="text-muted-foreground">=</span>
						<span class="tabular-nums text-base font-bold leading-none {agentJobCount > 0 ? 'text-primary' : 'text-muted-foreground/50'}">{agentJobCount}</span>
						<span class="text-muted-foreground">runs</span>
					</div>
					<Button
						class="ml-auto"
						onclick={submitAgent}
						disabled={agentSubmitting || !selectedStackSlug || agentJobCount === 0}
						title={agentModelIds.size === 0 ? 'Select at least one model' : agentJobCount === 0 ? 'Select an app spec or describe a task' : undefined}
					>
						{#if agentSubmitting}
							<LoaderCircle class="animate-spin" /> Starting…
						{:else}
							<Bot /> Start agent runs
						{/if}
					</Button>
				</div>
			</div>
		</Card.Content>
	</Card.Root>
{/if}

<!-- Prompt preview dialog: the exact prompts a job for this app+profile would freeze -->
<Dialog.Root bind:open={promptPreviewOpen}>
	<Dialog.Content class="max-w-3xl max-h-[85vh] overflow-y-auto">
		<Dialog.Header>
			<Dialog.Title>Prompt preview</Dialog.Title>
			<Dialog.Description>
				{previewableSlug}{promptPreview?.version ? `@${promptPreview.version}` : ''} rendered for {previewAppSlug} — what a job snapshot would freeze.
			</Dialog.Description>
		</Dialog.Header>
		{#if promptPreviewLoading}
			<LoadingState label="rendering" class="py-8" />
		{:else if promptPreviewError}
			<ErrorAlert message={promptPreviewError} />
		{:else if promptPreview?.rendered}
			<Tabs.Root bind:value={promptPreviewStage}>
				<Tabs.List>
					<Tabs.Trigger value="backend">Backend</Tabs.Trigger>
					<Tabs.Trigger value="frontend">Frontend</Tabs.Trigger>
				</Tabs.List>
			<Tabs.Content value={promptPreviewStage} class="space-y-3">
				{#each ['system', 'user'] as role}
					{@const text = promptPreview.rendered[promptPreviewStage as 'backend' | 'frontend']?.[role as 'system' | 'user'] ?? ''}
					<div>
						<p class="section-label mb-1">{role} ({text.length.toLocaleString()} chars)</p>
						<pre class="max-h-64 overflow-auto rounded border bg-muted/20 p-3 text-[11px] font-mono leading-relaxed whitespace-pre-wrap">{text}</pre>
					</div>
				{/each}
				{#if promptPreview.effective_llm && Object.keys(promptPreview.effective_llm).length > 0}
					<div class="text-[11px] text-muted-foreground font-mono">
						llm: {JSON.stringify(promptPreview.effective_llm)}
					</div>
				{/if}
			</Tabs.Content>
			</Tabs.Root>
		{/if}
	</Dialog.Content>
</Dialog.Root>

{#snippet stackPicker()}
	<!-- Stack pills -->
	<div class="space-y-2">
		<Label class="text-xs">Stack</Label>
		{#if scaffoldingLoading}
			<LoadingState label="loading stacks" class="py-4" />
		{:else if stacks.length === 0}
			<EmptyState compact title="No stacks found" description="Stacks are managed in the Generation Library." />
		{:else}
			<div class="flex flex-wrap gap-2">
				{#each stacks as stack}
					<button
						type="button"
						class="flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium transition-colors cursor-pointer
							{selectedStackSlug === stack.slug
								? 'border-primary bg-primary/10 text-primary'
								: 'border-border text-muted-foreground hover:border-foreground/30 hover:text-foreground'}"
						aria-pressed={selectedStackSlug === stack.slug}
						onclick={() => (selectedStackSlug = stack.slug)}
					>
						{stack.slug}
						{#if !stack.has_frontend}
							<span class="text-[11px] opacity-70">backend-only</span>
						{/if}
					</button>
				{/each}
			</div>
		{/if}
	</div>
{/snippet}

{#snippet appPicker()}
	<!-- App list -->
	<div class="min-w-0 space-y-2">
		<div class="flex items-center justify-between">
			<Label class="text-xs">Apps</Label>
			<div class="flex items-center gap-2 text-[11px]">
				<button type="button" class="text-primary hover:underline cursor-pointer" onclick={selectAllApps}>All</button>
				<span class="text-muted-foreground">·</span>
				<button type="button" class="text-muted-foreground hover:text-foreground hover:underline cursor-pointer" onclick={clearApps}>None</button>
				{#if selectedAppIds.size > 0}
					<Badge variant="secondary" class="ml-1">{selectedAppIds.size}</Badge>
				{/if}
			</div>
		</div>
		{#if appCategories.length > 1}
			<div class="flex gap-1 overflow-x-auto pb-0.5 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
				<button
					type="button"
					class="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium transition-colors cursor-pointer {categoryFilter === '' ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground hover:text-foreground'}"
					aria-pressed={categoryFilter === ''}
					onclick={() => (categoryFilter = '')}
				>All</button>
				{#each appCategories as cat}
					<button
						type="button"
						class="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium transition-colors cursor-pointer {categoryFilter === cat ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground hover:text-foreground'}"
						aria-pressed={categoryFilter === cat}
						onclick={() => (categoryFilter = categoryFilter === cat ? '' : cat)}
					>{cat}</button>
				{/each}
			</div>
		{/if}
		<div class="relative">
			<Search class="absolute left-2.5 top-2 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
			<Input bind:value={appSearch} placeholder="Filter apps…" aria-label="Filter apps" class="h-7 pl-8 text-xs" />
		</div>
		<div class="max-h-60 overflow-y-auto rounded-md border divide-y divide-border/50">
			{#if scaffoldingLoading}
				<LoadingState label="loading apps" class="py-6" />
			{:else}
				{#each filteredAppTemplates as app}
					<button
						type="button"
						class="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted/40 cursor-pointer {selectedAppIds.has(app.id) ? 'bg-primary/5' : ''}"
						aria-pressed={selectedAppIds.has(app.id)}
						onclick={() => toggleAppTemplate(app.id)}
					>
						<div class="flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border transition-colors {selectedAppIds.has(app.id) ? 'bg-primary border-primary text-primary-foreground' : 'border-muted-foreground/40'}">
							{#if selectedAppIds.has(app.id)}<Check class="h-2.5 w-2.5" />{/if}
						</div>
						<span class="font-medium">{app.name}</span>
						<Badge variant="secondary" class="ml-auto shrink-0">{app.category}</Badge>
					</button>
				{/each}
				{#if filteredAppTemplates.length === 0}
					<EmptyState compact title="No apps found" description="Clear the filter or add app specs in the Generation Library." />
				{/if}
			{/if}
		</div>
	</div>
{/snippet}
