<script lang="ts">
	import { onMount } from 'svelte';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Settings from '@lucide/svelte/icons/settings';
	import {
		getModels,
		getStacks,
		getAppTemplates,
		type LLMModelSummary,
		type Stack,
		type AppRequirementTemplate
	} from '$lib/api/client';
	import { getAnalysisProfiles, type AnalysisProfile } from '$lib/api/analysis';
	import type { Edge } from '@xyflow/svelte';
	import type { WorkflowNode } from '$lib/utils/pipeline-flow';

	interface Props {
		nodes: WorkflowNode[];
		edges: Edge[];
		selectedNodeId: string | null;
	}

	let { nodes = $bindable(), edges = $bindable(), selectedNodeId = $bindable() }: Props = $props();

	const KINDS = ['generate', 'analyze', 'report', 'wait', 'notify', 'script'] as const;
	const ANALYZERS = ['bandit', 'eslint', 'pylint', 'zap', 'port_scanner', 'lighthouse', 'llm_review'];
	const REPORT_TYPES = [
		{ value: 'comprehensive', label: 'Comprehensive' },
		{ value: 'model_analysis', label: 'Model Analysis' },
		{ value: 'template_comparison', label: 'Template Comparison' },
		{ value: 'tool_analysis', label: 'Tool Analysis' },
		{ value: 'generation_analytics', label: 'Generation Analytics' }
	];
	const SCRIPT_OPERATIONS = [
		{ value: 'noop', label: 'No-op (placeholder)' },
		{ value: 'log_message', label: 'Log message' },
		{ value: 'record_metric', label: 'Record metric' }
	];

	let models = $state<LLMModelSummary[]>([]);
	let stacks = $state<Stack[]>([]);
	let appTemplates = $state<AppRequirementTemplate[]>([]);
	let analysisProfiles = $state<AnalysisProfile[]>([]);

	const selected = $derived(nodes.find((n) => n.id === selectedNodeId) ?? null);
	const cfg = $derived(((selected?.data.config ?? {}) as Record<string, unknown>));
	const scriptText = $derived(JSON.stringify(stripPosition(cfg), null, 2));
	let scriptDraft = $state('');
	let scriptError = $state('');

	$effect(() => {
		scriptDraft = scriptText;
		scriptError = '';
	});

	function stripPosition(c: Record<string, unknown>): Record<string, unknown> {
		const { _position, ...rest } = c;
		void _position;
		return rest;
	}

	function updateNode(updater: (n: WorkflowNode) => WorkflowNode) {
		if (!selected) return;
		nodes = nodes.map((n) => (n.id === selected.id ? updater(n) : n));
	}

	function updateConfig(key: string, value: unknown) {
		updateNode((n) => {
			const newCfg = { ...(n.data.config as Record<string, unknown>), [key]: value };
			return { ...n, data: { ...n.data, config: newCfg } };
		});
	}

	function unsetConfig(key: string) {
		updateNode((n) => {
			const newCfg = { ...(n.data.config as Record<string, unknown>) };
			delete newCfg[key];
			return { ...n, data: { ...n.data, config: newCfg } };
		});
	}

	function renameNode(newName: string) {
		if (!selected) return;
		const trimmed = newName.trim();
		if (!trimmed || trimmed === selected.id) return;
		if (nodes.some((n) => n.id === trimmed)) return;
		const oldId = selected.id;
		nodes = nodes.map((n) =>
			n.id === oldId ? { ...n, id: trimmed, data: { ...n.data, name: trimmed } } : n
		);
		edges = edges.map((e) => ({
			...e,
			source: e.source === oldId ? trimmed : e.source,
			target: e.target === oldId ? trimmed : e.target,
			id: e.id.replace(new RegExp(`(^|>)${escapeRegex(oldId)}($|->)`), `$1${trimmed}$2`)
		}));
		selectedNodeId = trimmed;
	}

	function escapeRegex(s: string): string {
		return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
	}

	function changeKind(newKind: string) {
		updateNode((n) => ({
			...n,
			type: newKind as WorkflowNode['type'],
			data: { ...n.data, kind: newKind as WorkflowNode['data']['kind'], config: {} }
		}));
	}

	function setMaxRetries(v: number) {
		updateNode((n) => ({ ...n, data: { ...n.data, max_retries: v } }));
	}

	function toggleAnalyzer(slug: string) {
		const list = ((cfg.analyzers as string[]) ?? []) as string[];
		const next = list.includes(slug) ? list.filter((a) => a !== slug) : [...list, slug];
		updateConfig('analyzers', next);
	}

	function deleteNode() {
		if (!selected) return;
		const id = selected.id;
		nodes = nodes.filter((n) => n.id !== id);
		edges = edges.filter((e) => e.source !== id && e.target !== id);
		selectedNodeId = null;
	}

	function commitScriptDraft() {
		try {
			const parsed = JSON.parse(scriptDraft);
			if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
				scriptError = 'Top-level must be a JSON object';
				return;
			}
			updateNode((n) => {
				const pos = (n.data.config as Record<string, unknown>)._position;
				return { ...n, data: { ...n.data, config: { ...parsed, _position: pos } } };
			});
			scriptError = '';
		} catch (e) {
			scriptError = (e as Error).message;
		}
	}

	onMount(async () => {
		try {
			const [m, s, a, p] = await Promise.allSettled([
				getModels({ per_page: 200 }),
				getStacks(),
				getAppTemplates(),
				getAnalysisProfiles()
			]);
			if (m.status === 'fulfilled') models = m.value.items;
			if (s.status === 'fulfilled') stacks = s.value;
			if (a.status === 'fulfilled') appTemplates = a.value;
			if (p.status === 'fulfilled') analysisProfiles = p.value;
		} catch {
			/* ignore */
		}
	});
</script>

<aside class="flex flex-col h-full w-80 shrink-0 border-l bg-card/40 overflow-y-auto">
	<div class="px-4 py-3 border-b flex items-center justify-between">
		<div>
			<h2 class="text-sm font-semibold flex items-center gap-1.5">
				<Settings class="h-4 w-4" /> Properties
			</h2>
			<p class="text-xs text-muted-foreground mt-0.5">
				{selected ? 'Editing selected node' : 'Select a node to edit'}
			</p>
		</div>
		{#if selected}
			<Button variant="ghost" size="icon" class="h-7 w-7 text-destructive hover:text-destructive" onclick={deleteNode} title="Delete node">
				<Trash2 class="h-4 w-4" />
			</Button>
		{/if}
	</div>

	{#if !selected}
		<div class="flex-1 flex items-center justify-center p-6 text-center">
			<p class="text-xs text-muted-foreground">Click a node on the canvas to edit its configuration here.</p>
		</div>
	{:else}
		<div class="p-4 space-y-4 text-sm">
			<div class="space-y-1.5">
				<Label class="text-xs">Step Name</Label>
				<input
					type="text"
					value={selected.data.name}
					onchange={(e) => renameNode((e.target as HTMLInputElement).value)}
					class="w-full rounded-md border bg-background px-2 py-1.5 text-sm font-mono"
				/>
				<p class="text-[10px] text-muted-foreground">Must be unique. Renaming updates dependencies.</p>
			</div>

			<div class="space-y-1.5">
				<Label class="text-xs">Kind</Label>
				<select
					value={selected.data.kind}
					onchange={(e) => changeKind((e.target as HTMLSelectElement).value)}
					class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
				>
					{#each KINDS as k}<option value={k}>{k}</option>{/each}
				</select>
			</div>

			<div class="space-y-1.5">
				<Label class="text-xs">Max Retries</Label>
				<input
					type="number"
					value={selected.data.max_retries}
					oninput={(e) => setMaxRetries(parseInt((e.target as HTMLInputElement).value) || 0)}
					min="0"
					max="10"
					class="w-full rounded-md border bg-background px-2 py-1.5 text-sm"
				/>
			</div>

			<div class="border-t pt-4 space-y-3">
				<p class="text-xs uppercase tracking-wider text-muted-foreground font-medium">Configuration</p>

				{#if selected.data.kind === 'generate'}
					<div class="space-y-1.5">
						<Label class="text-xs">Model</Label>
						<select
							value={(cfg.model_id as string) ?? ''}
							onchange={(e) => updateConfig('model_id', (e.target as HTMLSelectElement).value || undefined)}
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						>
							<option value="">— Select model —</option>
							{#each models as m (m.canonical_slug)}<option value={m.canonical_slug}>{m.model_name}</option>{/each}
						</select>
					</div>
					<div class="space-y-1.5">
						<Label class="text-xs">Template</Label>
						<select
							value={(cfg.template_slug as string) ?? ''}
							onchange={(e) => updateConfig('template_slug', (e.target as HTMLSelectElement).value || undefined)}
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						>
							<option value="">— Select template —</option>
							<optgroup label="Scaffolding">
								{#each stacks as s (s.slug)}<option value={s.slug}>{s.slug}</option>{/each}
							</optgroup>
							<optgroup label="App requirements">
								{#each appTemplates as t (t.slug)}<option value={t.slug}>{t.name}</option>{/each}
							</optgroup>
						</select>
					</div>
					<div class="space-y-1.5">
						<Label class="text-xs">App Number</Label>
						<input
							type="number"
							value={(cfg.app_num as number) ?? 1}
							oninput={(e) => updateConfig('app_num', parseInt((e.target as HTMLInputElement).value) || 1)}
							min="1"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						/>
					</div>
					<div class="space-y-1.5">
						<Label class="text-xs">Prompt Override</Label>
						<textarea
							value={(cfg.prompt as string) ?? ''}
							oninput={(e) => {
								const v = (e.target as HTMLTextAreaElement).value;
								v ? updateConfig('prompt', v) : unsetConfig('prompt');
							}}
							rows={3}
							placeholder="Leave empty to use template default"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-ring"
						></textarea>
					</div>

				{:else if selected.data.kind === 'analyze'}
					{#if analysisProfiles.length > 0}
						<div class="space-y-1.5">
							<Label class="text-xs">Profile</Label>
							<select
								value={(cfg.profile_id as number | null) ? String(cfg.profile_id) : ''}
								onchange={(e) => {
									const val = (e.target as HTMLSelectElement).value;
									if (!val) {
										updateConfig('profile_id', null);
										updateConfig('profile_name', null);
									} else {
										const profile = analysisProfiles.find((p) => String(p.id) === val);
										updateConfig('profile_id', profile?.id ?? null);
										updateConfig('profile_name', profile?.name ?? null);
									}
								}}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								<option value="">— No profile —</option>
								{#each analysisProfiles as p (p.id)}
									<option value={String(p.id)}>{p.name}{p.is_default ? ' (default)' : ''}</option>
								{/each}
							</select>
							<p class="text-[10px] text-muted-foreground">Analyzer toggles below override profile defaults.</p>
						</div>
					{/if}
					<div class="space-y-1.5">
						<Label class="text-xs">Analyzers</Label>
						<div class="flex flex-wrap gap-1.5">
							{#each ANALYZERS as a (a)}
								{@const on = ((cfg.analyzers as string[]) ?? []).includes(a)}
								<button
									type="button"
									onclick={() => toggleAnalyzer(a)}
									class="rounded-full border px-2.5 py-0.5 text-xs font-medium transition-colors {on ? 'bg-primary text-primary-foreground border-primary' : 'bg-background text-muted-foreground hover:border-primary/50'}"
								>{a}</button>
							{/each}
						</div>
					</div>
					<div class="space-y-1.5">
						<Label class="text-xs">Generation Job Ref</Label>
						<input
							type="text"
							value={(cfg.generation_job as string) ?? ''}
							oninput={(e) => updateConfig('generation_job', (e.target as HTMLInputElement).value)}
							placeholder={'{steps.generate.output.generation_job_id}'}
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs font-mono"
						/>
					</div>
					<label class="flex items-center gap-2 text-xs cursor-pointer">
						<input
							type="checkbox"
							checked={(cfg.live_target as boolean) ?? false}
							onchange={(e) => updateConfig('live_target', (e.target as HTMLInputElement).checked)}
							class="rounded"
						/>
						Live target (running container)
					</label>

				{:else if selected.data.kind === 'report'}
					<div class="space-y-1.5">
						<Label class="text-xs">Report Type</Label>
						<select
							value={(cfg.report_type as string) ?? 'comprehensive'}
							onchange={(e) => updateConfig('report_type', (e.target as HTMLSelectElement).value)}
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						>
							{#each REPORT_TYPES as t (t.value)}<option value={t.value}>{t.label}</option>{/each}
						</select>
						<p class="text-[10px] text-muted-foreground">Determines which generator runs.</p>
					</div>
					{#if cfg.report_type === 'template_comparison'}
						<div class="space-y-1.5">
							<Label class="text-xs">Template (required)</Label>
							<select
								value={(cfg.template_slug as string) ?? ''}
								onchange={(e) => updateConfig('template_slug', (e.target as HTMLSelectElement).value || undefined)}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								<option value="">— Select template —</option>
								{#each appTemplates as t (t.slug)}<option value={t.slug}>{t.name}</option>{/each}
							</select>
						</div>
					{/if}
					{#if cfg.report_type === 'model_analysis'}
						<div class="space-y-1.5">
							<Label class="text-xs">Model (required)</Label>
							<select
								value={(cfg.model_id as string) ?? ''}
								onchange={(e) => updateConfig('model_id', (e.target as HTMLSelectElement).value || undefined)}
								class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
							>
								<option value="">— Select model —</option>
								{#each models as m (m.canonical_slug)}<option value={m.canonical_slug}>{m.model_name}</option>{/each}
							</select>
						</div>
					{/if}
					<div class="space-y-1.5">
						<Label class="text-xs">Title (optional)</Label>
						<input
							type="text"
							value={(cfg.title as string) ?? ''}
							oninput={(e) => {
								const v = (e.target as HTMLInputElement).value;
								v ? updateConfig('title', v) : unsetConfig('title');
							}}
							placeholder="Auto-generated if blank"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						/>
					</div>

				{:else if selected.data.kind === 'wait'}
					<div class="space-y-1.5">
						<Label class="text-xs">Duration (seconds)</Label>
						<input
							type="number"
							value={(cfg.seconds as number) ?? 5}
							oninput={(e) => updateConfig('seconds', parseInt((e.target as HTMLInputElement).value) || 1)}
							min="1"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						/>
					</div>

				{:else if selected.data.kind === 'notify'}
					<div class="space-y-1.5">
						<Label class="text-xs">Channel</Label>
						<input
							type="text"
							value={(cfg.channel as string) ?? 'general'}
							oninput={(e) => updateConfig('channel', (e.target as HTMLInputElement).value || 'general')}
							placeholder="general"
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						/>
					</div>
					<div class="space-y-1.5">
						<Label class="text-xs">Message</Label>
						<textarea
							value={(cfg.message as string) ?? ''}
							oninput={(e) => updateConfig('message', (e.target as HTMLTextAreaElement).value)}
							rows={4}
							placeholder="Notification message..."
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs resize-y focus:outline-none focus:ring-1 focus:ring-ring"
						></textarea>
					</div>

				{:else if selected.data.kind === 'script'}
					<div class="space-y-1.5">
						<Label class="text-xs">Operation</Label>
						<select
							value={(cfg.code as string) ?? 'noop'}
							onchange={(e) => updateConfig('code', (e.target as HTMLSelectElement).value)}
							class="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
						>
							{#each SCRIPT_OPERATIONS as op (op.value)}<option value={op.value}>{op.label}</option>{/each}
						</select>
						<p class="text-[10px] text-muted-foreground">Only allowlisted operations run; arbitrary code is rejected.</p>
					</div>
					<details class="space-y-1.5">
						<summary class="text-xs cursor-pointer text-muted-foreground hover:text-foreground">Advanced: raw JSON</summary>
						<textarea
							bind:value={scriptDraft}
							rows={8}
							class="w-full mt-2 rounded-md border bg-background px-2 py-1.5 text-xs font-mono resize-y focus:outline-none focus:ring-1 focus:ring-ring"
						></textarea>
						{#if scriptError}
							<p class="text-xs text-destructive">{scriptError}</p>
						{/if}
						<Button variant="outline" size="sm" class="w-full" onclick={commitScriptDraft}>Apply JSON</Button>
					</details>
				{/if}
			</div>
		</div>
	{/if}
</aside>
