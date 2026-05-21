<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import ChevronUp from '@lucide/svelte/icons/chevron-up';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import { getModels, getScaffoldingTemplates, getAppTemplates, type LLMModelSummary, type ScaffoldingTemplate, type AppRequirementTemplate } from '$lib/api/client';

	interface StepConfig {
		// generate
		model_id?: string;
		template_slug?: string;
		app_num?: number;
		prompt?: string;
		// analyze
		analyzers?: string[];
		generation_job?: string;
		live_target?: boolean;
		// report
		format?: string;
		inputs?: string;
		// wait
		seconds?: number;
		// notify
		message?: string;
	}

	interface EditableStep {
		_id: string;
		name: string;
		kind: string;
		config: StepConfig;
		depends_on: string[];
		max_retries: number;
	}

	interface PipelineConfig {
		steps: Array<{
			name: string;
			kind: string;
			order: number;
			config: StepConfig;
			depends_on: string[];
			max_retries: number;
		}>;
	}

	let {
		value = $bindable<PipelineConfig>({ steps: [] }),
		errors = $bindable<string[]>([])
	} = $props();

	const KINDS = ['generate', 'analyze', 'report', 'wait', 'notify', 'script'] as const;
	const ANALYZERS = ['bandit', 'eslint', 'pylint', 'zap', 'port_scanner', 'lighthouse', 'llm_review'];
	const REPORT_FORMATS = ['PDF', 'HTML', 'JSON'];

	let steps = $state<EditableStep[]>([]);
	let models = $state<LLMModelSummary[]>([]);
	let scaffoldingTemplates = $state<ScaffoldingTemplate[]>([]);
	let appTemplates = $state<AppRequirementTemplate[]>([]);

	let uid = 0;
	function newId() { return `step-${++uid}`; }

	function configToSteps(cfg: PipelineConfig): EditableStep[] {
		return (cfg?.steps ?? []).map((s, i) => ({
			_id: newId(),
			name: s.name ?? `step_${i + 1}`,
			kind: s.kind ?? 'script',
			config: (s.config as StepConfig) ?? {},
			depends_on: s.depends_on ?? [],
			max_retries: s.max_retries ?? 0,
		}));
	}

	function stepsToConfig(): PipelineConfig {
		return {
			steps: steps.map((s, i) => ({
				name: s.name,
				kind: s.kind,
				order: i,
				config: s.config,
				depends_on: s.depends_on,
				max_retries: s.max_retries,
			})),
		};
	}

	function sync() {
		value = stepsToConfig();
		validate();
	}

	function validate() {
		const errs: string[] = [];
		const names = steps.map((s) => s.name.trim());
		names.forEach((n, i) => {
			if (!n) errs.push(`Step ${i + 1}: name is required`);
			if (!steps[i].kind) errs.push(`Step ${i + 1}: kind is required`);
		});
		const seen = new Set<string>();
		names.forEach((n) => {
			if (n && seen.has(n)) errs.push(`Duplicate step name: "${n}"`);
			seen.add(n);
		});
		errors = errs;
	}

	function addStep() {
		const idx = steps.length + 1;
		steps = [
			...steps,
			{
				_id: newId(),
				name: `step_${idx}`,
				kind: 'generate',
				config: {},
				depends_on: [],
				max_retries: 0,
			},
		];
		sync();
	}

	function removeStep(idx: number) {
		const name = steps[idx].name;
		steps = steps.filter((_, i) => i !== idx).map((s) => ({
			...s,
			depends_on: s.depends_on.filter((d) => d !== name),
		}));
		sync();
	}

	function moveUp(idx: number) {
		if (idx === 0) return;
		const arr = [...steps];
		[arr[idx - 1], arr[idx]] = [arr[idx], arr[idx - 1]];
		steps = arr;
		sync();
	}

	function moveDown(idx: number) {
		if (idx >= steps.length - 1) return;
		const arr = [...steps];
		[arr[idx], arr[idx + 1]] = [arr[idx + 1], arr[idx]];
		steps = arr;
		sync();
	}

	function toggleAnalyzer(step: EditableStep, analyzer: string) {
		const list = step.config.analyzers ?? [];
		if (list.includes(analyzer)) {
			step.config.analyzers = list.filter((a) => a !== analyzer);
		} else {
			step.config.analyzers = [...list, analyzer];
		}
		sync();
	}

	// Initialize steps from incoming value
	$effect(() => {
		steps = configToSteps(value);
	});

	onMount(async () => {
		try {
			const [modelsRes, scaffRes, appRes] = await Promise.allSettled([
				getModels({ per_page: 200 }),
				getScaffoldingTemplates(),
				getAppTemplates(),
			]);
			if (modelsRes.status === 'fulfilled') models = modelsRes.value.items;
			if (scaffRes.status === 'fulfilled') scaffoldingTemplates = scaffRes.value;
			if (appRes.status === 'fulfilled') appTemplates = appRes.value;
		} catch { /* silently handle */ }
	});
</script>

<div class="space-y-3">
	{#each steps as step, idx (step._id)}
		<div class="rounded-lg border bg-card shadow-xs">
			<!-- Step header -->
			<div class="flex items-center gap-2 border-b px-4 py-2.5 bg-muted/30 rounded-t-lg">
				<span class="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground text-xs font-medium">{idx + 1}</span>
				<div class="flex-1 min-w-0">
					<input
						type="text"
						value={step.name}
						oninput={(e) => { step.name = (e.target as HTMLInputElement).value; sync(); }}
						placeholder="step_name"
						class="w-full bg-transparent text-sm font-medium outline-none placeholder:text-muted-foreground"
					/>
				</div>
				<select
					value={step.kind}
					onchange={(e) => { step.kind = (e.target as HTMLSelectElement).value; step.config = {}; sync(); }}
					class="rounded-md border bg-background px-2 py-1 text-xs"
				>
					{#each KINDS as k}<option value={k}>{k}</option>{/each}
				</select>
				<div class="flex items-center gap-1">
					<Button variant="ghost" size="icon" class="h-6 w-6" onclick={() => moveUp(idx)} disabled={idx === 0}>
						<ChevronUp class="h-3 w-3" />
					</Button>
					<Button variant="ghost" size="icon" class="h-6 w-6" onclick={() => moveDown(idx)} disabled={idx >= steps.length - 1}>
						<ChevronDown class="h-3 w-3" />
					</Button>
					<Button variant="ghost" size="icon" class="h-6 w-6 text-destructive hover:text-destructive" onclick={() => removeStep(idx)}>
						<Trash2 class="h-3 w-3" />
					</Button>
				</div>
			</div>

			<!-- Step body -->
			<div class="px-4 py-3 space-y-3">
				<!-- Kind-specific config -->
				{#if step.kind === 'generate'}
					<div class="grid grid-cols-2 gap-3">
						<div class="space-y-1">
							<Label class="text-xs">Model</Label>
							<select
								value={step.config.model_id ?? ''}
								onchange={(e) => { step.config.model_id = (e.target as HTMLSelectElement).value; sync(); }}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								<option value="">— Select model —</option>
								{#each models as m}<option value={m.slug}>{m.name}</option>{/each}
							</select>
						</div>
						<div class="space-y-1">
							<Label class="text-xs">Template</Label>
							<select
								value={step.config.template_slug ?? ''}
								onchange={(e) => { step.config.template_slug = (e.target as HTMLSelectElement).value; sync(); }}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								<option value="">— Select template —</option>
								{#each scaffoldingTemplates as t}<option value={t.slug}>{t.name}</option>{/each}
								{#each appTemplates as t}<option value={t.slug}>{t.name}</option>{/each}
							</select>
						</div>
						<div class="space-y-1">
							<Label class="text-xs">App Number</Label>
							<input
								type="number"
								value={step.config.app_num ?? 1}
								oninput={(e) => { step.config.app_num = parseInt((e.target as HTMLInputElement).value) || 1; sync(); }}
								min="1"
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							/>
						</div>
					</div>
					<div class="space-y-1">
						<Label class="text-xs">Prompt Override (optional)</Label>
						<textarea
							value={step.config.prompt ?? ''}
							oninput={(e) => { step.config.prompt = (e.target as HTMLTextAreaElement).value; sync(); }}
							rows={2}
							placeholder="Leave empty to use template default"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-ring"
						></textarea>
					</div>

				{:else if step.kind === 'analyze'}
					<div class="space-y-1">
						<Label class="text-xs">Analyzers</Label>
						<div class="flex flex-wrap gap-1.5">
							{#each ANALYZERS as a}
								<button
									type="button"
									onclick={() => { toggleAnalyzer(step, a); }}
									class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors {(step.config.analyzers ?? []).includes(a) ? 'bg-primary text-primary-foreground border-primary' : 'bg-background text-muted-foreground hover:border-primary/50'}"
								>{a}</button>
							{/each}
						</div>
					</div>
					<div class="grid grid-cols-2 gap-3">
						<div class="space-y-1">
							<Label class="text-xs">Generation Job Ref</Label>
							<input
								type="text"
								value={step.config.generation_job ?? ''}
								oninput={(e) => { step.config.generation_job = (e.target as HTMLInputElement).value; sync(); }}
								placeholder={"{steps.generate.output.generation_job_id}"}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs font-mono"
							/>
						</div>
						<div class="space-y-1 flex flex-col justify-end">
							<label class="flex items-center gap-2 text-xs cursor-pointer">
								<input
									type="checkbox"
									checked={step.config.live_target ?? false}
									onchange={(e) => { step.config.live_target = (e.target as HTMLInputElement).checked; sync(); }}
									class="rounded"
								/>
								Live target (running container)
							</label>
						</div>
					</div>

				{:else if step.kind === 'report'}
					<div class="grid grid-cols-2 gap-3">
						<div class="space-y-1">
							<Label class="text-xs">Template</Label>
							<select
								value={step.config.template_slug ?? ''}
								onchange={(e) => { step.config.template_slug = (e.target as HTMLSelectElement).value; sync(); }}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								<option value="">— Select template —</option>
								{#each appTemplates as t}<option value={t.slug}>{t.name}</option>{/each}
							</select>
						</div>
						<div class="space-y-1">
							<Label class="text-xs">Format</Label>
							<select
								value={step.config.format ?? 'PDF'}
								onchange={(e) => { step.config.format = (e.target as HTMLSelectElement).value; sync(); }}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								{#each REPORT_FORMATS as f}<option value={f}>{f}</option>{/each}
							</select>
						</div>
					</div>
					<div class="space-y-1">
						<Label class="text-xs">Inputs (analysis_task_id ref)</Label>
						<input
							type="text"
							value={step.config.inputs ?? ''}
							oninput={(e) => { step.config.inputs = (e.target as HTMLInputElement).value; sync(); }}
							placeholder={"{steps.analyze.output.analysis_task_id}"}
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs font-mono"
						/>
					</div>

				{:else if step.kind === 'wait'}
					<div class="space-y-1 max-w-xs">
						<Label class="text-xs">Duration (seconds)</Label>
						<input
							type="number"
							value={step.config.seconds ?? 5}
							oninput={(e) => { step.config.seconds = parseInt((e.target as HTMLInputElement).value) || 5; sync(); }}
							min="1"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						/>
					</div>

				{:else if step.kind === 'notify'}
					<div class="space-y-1">
						<Label class="text-xs">Message</Label>
						<textarea
							value={step.config.message ?? ''}
							oninput={(e) => { step.config.message = (e.target as HTMLTextAreaElement).value; sync(); }}
							rows={2}
							placeholder="Notification message..."
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-ring"
						></textarea>
					</div>

				{:else if step.kind === 'script'}
					<div class="rounded-md bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
						⚙️ <em>Advanced — script steps are configured via JSON editor.</em>
					</div>
				{/if}

				<!-- Depends on + max_retries -->
				<div class="grid grid-cols-2 gap-3 pt-1 border-t">
					<div class="space-y-1">
						<Label class="text-xs text-muted-foreground">Depends on</Label>
						{#if steps.filter((_, i) => i !== idx).length === 0}
							<p class="text-xs text-muted-foreground italic">No other steps</p>
						{:else}
							<div class="flex flex-wrap gap-1.5">
								{#each steps.filter((_, i) => i !== idx) as other}
									<button
										type="button"
										onclick={() => {
											const hasIt = step.depends_on.includes(other.name);
											step.depends_on = hasIt
												? step.depends_on.filter((d) => d !== other.name)
												: [...step.depends_on, other.name];
											sync();
										}}
										class="rounded-full border px-2.5 py-0.5 text-xs transition-colors {step.depends_on.includes(other.name) ? 'bg-primary text-primary-foreground border-primary' : 'bg-background text-muted-foreground hover:border-primary/50'}"
									>{other.name}</button>
								{/each}
							</div>
						{/if}
					</div>
					<div class="space-y-1">
						<Label class="text-xs text-muted-foreground">Max Retries</Label>
						<input
							type="number"
							value={step.max_retries}
							oninput={(e) => { step.max_retries = parseInt((e.target as HTMLInputElement).value) || 0; sync(); }}
							min="0"
							max="10"
							class="w-full max-w-[80px] rounded-md border bg-background px-2 py-1.5 text-xs"
						/>
					</div>
				</div>
			</div>
		</div>
	{/each}

	<Button variant="outline" size="sm" onclick={addStep} class="w-full border-dashed">
		<Plus class="mr-2 h-3.5 w-3.5" />
		Add Step
	</Button>

	{#if errors.length > 0}
		<div class="rounded-md border border-destructive/50 bg-destructive/10 p-2.5 text-xs text-destructive space-y-0.5">
			{#each errors as e}<p>• {e}</p>{/each}
		</div>
	{/if}
</div>
