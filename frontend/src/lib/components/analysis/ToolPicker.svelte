<script lang="ts">
	/**
	 * ToolPicker — choose the workspace tools a run uses, grouped by category,
	 * with quick sets (all installed, static only, security & dependencies) and
	 * per-group toggles. Each row says what the tool reads and, from the
	 * preview, where it would actually run ("3 of 4 apps", "nothing to read",
	 * "app not running"). Tools that aren't installed are listed but disabled
	 * (install them in Analyzers); dynamic tools are disabled for pasted code.
	 *
	 * Props: `tools` (catalog), `selected` (bindable slugs, replaced on change),
	 * `preview` (null while unknown), `targets` (number of targets), `codeMode`.
	 */
	import type { AnalyzerTool } from '$lib/api/analyzers';
	import type { RunPreview } from '$lib/api/runs';
	import { Button } from '$lib/components/ui/button';
	import Check from '@lucide/svelte/icons/check';
	import Minus from '@lucide/svelte/icons/minus';
	import Globe from '@lucide/svelte/icons/globe';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import ToolIcon from '$lib/components/analyzers/ToolIcon.svelte';
	import { cn } from '$lib/utils';
	import { categoryLabel, groupByCategory } from './tools';

	interface Props {
		tools: AnalyzerTool[];
		selected: string[];
		preview: RunPreview | null;
		targets: number;
		codeMode: boolean;
	}

	let { tools, selected = $bindable(), preview, targets, codeMode }: Props = $props();

	const selectedSet = $derived(new Set(selected));
	const previewBySlug = $derived(new Map((preview?.tools ?? []).map((t) => [t.slug, t])));

	function usable(t: AnalyzerTool): boolean {
		return t.installed && t.is_enabled && !(codeMode && t.analysis_mode === 'dynamic');
	}

	function why(t: AnalyzerTool): string {
		if (!t.installed) return t.install_status === 'failed' ? 'Install failed' : 'Not installed';
		if (!t.is_enabled) return 'Disabled';
		if (codeMode && t.analysis_mode === 'dynamic') return 'Needs a running app';
		return '';
	}

	const groups = $derived(groupByCategory(tools));
	const usableTools = $derived(tools.filter(usable));

	function set(slugs: string[]) {
		// Keep catalog order (the run executes tools in the order given).
		const want = new Set(slugs);
		selected = tools.filter((t) => want.has(t.slug) && usable(t)).map((t) => t.slug);
	}

	function toggle(t: AnalyzerTool) {
		if (!usable(t)) return;
		set(selectedSet.has(t.slug) ? selected.filter((s) => s !== t.slug) : [...selected, t.slug]);
	}

	function toggleGroup(items: AnalyzerTool[]) {
		const usableItems = items.filter(usable);
		const all = usableItems.every((t) => selectedSet.has(t.slug));
		const slugs = new Set(usableItems.map((t) => t.slug));
		set(all ? selected.filter((s) => !slugs.has(s)) : [...selected, ...slugs]);
	}

	const presets = $derived([
		{ label: 'All installed', slugs: usableTools.filter((t) => t.kind !== 'ai').map((t) => t.slug), title: 'Every installed tool except the AI reviewer (it costs tokens)' },
		{ label: 'Static only', slugs: usableTools.filter((t) => t.analysis_mode !== 'dynamic' && t.kind !== 'ai').map((t) => t.slug), title: 'Tools that read the source; no running app needed' },
		{ label: 'Security & deps', slugs: usableTools.filter((t) => ['security', 'dependencies', 'secrets'].includes(t.category)).map((t) => t.slug), title: 'Security scanners, dependency auditors and secret detectors' },
	]);

	function where(t: AnalyzerTool): { text: string; tone: string } | null {
		const p = previewBySlug.get(t.slug);
		if (!p || targets === 0) return null;
		const unit = codeMode ? '' : ` of ${targets} app${targets === 1 ? '' : 's'}`;
		if (p.runs === targets) {
			const text = codeMode ? 'Will run' : targets === 1 ? 'Will run on this app' : `Runs on all ${targets} apps`;
			return { text, tone: 'text-success-fg' };
		}
		if (p.runs === 0 && p.app_not_running > 0) return { text: 'App not running — would be skipped', tone: 'text-warning-fg' };
		if (p.runs === 0) return { text: 'Nothing to read — would be skipped', tone: 'text-muted-foreground' };
		const parts = [`Runs on ${p.runs}${unit}`];
		if (p.not_applicable) parts.push(`${p.not_applicable} nothing to read`);
		if (p.app_not_running) parts.push(`${p.app_not_running} not running`);
		return { text: parts.join(' · '), tone: 'text-foreground/80' };
	}
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-center gap-1.5">
		<span class="mr-1 text-xs text-muted-foreground">Quick sets:</span>
		{#each presets as p (p.label)}
			<Button variant="outline" size="sm" class="h-7 text-xs" title={p.title} disabled={p.slugs.length === 0} onclick={() => set(p.slugs)}>
				{p.label}<span class="font-mono text-muted-foreground">{p.slugs.length}</span>
			</Button>
		{/each}
		<Button variant="ghost" size="sm" class="h-7 text-xs" disabled={selected.length === 0} onclick={() => set([])}>Clear</Button>
		<a href="/analyzers" target="_blank" rel="noopener" class="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline">
			Install more tools<ExternalLink class="size-3" />
		</a>
	</div>

	<div class="grid items-start gap-3 @2xl:grid-cols-2">
		{#each groups as group (group.category)}
			{@const groupUsable = group.items.filter(usable)}
			{@const groupSelected = groupUsable.filter((t) => selectedSet.has(t.slug)).length}
			<section class="min-w-0 overflow-hidden rounded-md border border-border" aria-label={categoryLabel(group.category)}>
				<header class="flex items-center gap-2 border-b border-border bg-muted/40 px-3 py-1.5">
					<h3 class="flex-1 text-xs font-medium">{categoryLabel(group.category)}</h3>
					<span class="font-mono text-[11px] text-muted-foreground tabular-nums">{groupSelected}/{groupUsable.length}</span>
					{#if groupUsable.length > 0}
						<button type="button" class="cursor-pointer text-[11px] text-primary hover:underline" onclick={() => toggleGroup(group.items)}>
							{groupSelected === groupUsable.length ? 'None' : 'All'}
						</button>
					{/if}
				</header>
				<ul>
					{#each group.items as tool (tool.slug)}
						{@const on = selectedSet.has(tool.slug)}
						{@const ok = usable(tool)}
						{@const reason = why(tool)}
						{@const w = on ? where(tool) : null}
						<li class="border-b border-border/60 last:border-b-0">
							<button
								type="button"
								role="checkbox"
								aria-checked={on}
								aria-disabled={!ok}
								aria-label="{tool.name}{reason ? ` (${reason.toLowerCase()})` : ''}"
								class={cn(
									'flex w-full items-start gap-2.5 px-3 py-2 text-left transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:outline-none focus-visible:ring-inset',
									ok ? 'cursor-pointer hover:bg-muted/50' : 'cursor-not-allowed opacity-60',
									on && 'bg-primary/5'
								)}
								onclick={() => toggle(tool)}
							>
								<span
									class={cn(
										'mt-1 flex size-4 shrink-0 items-center justify-center rounded-[4px] border transition-colors',
										on ? 'border-primary bg-primary text-primary-foreground' : 'border-input bg-background'
									)}
									aria-hidden="true"
								>
									{#if on}<Check class="size-3" />{/if}
								</span>
								<ToolIcon {tool} size="sm" />
								<span class="min-w-0 flex-1">
									<span class="flex flex-wrap items-center gap-1.5 text-sm font-medium">
										{tool.name}
										{#if tool.analysis_mode === 'dynamic'}
											<span class="inline-flex items-center gap-0.5 rounded border border-border px-1 text-[11px] font-normal text-muted-foreground"><Globe class="size-3" />Dynamic</span>
										{/if}
										{#if tool.kind === 'ai'}
											<span class="inline-flex items-center gap-0.5 rounded border border-border px-1 text-[11px] font-normal text-muted-foreground"><Sparkles class="size-3" />AI · costs tokens</span>
										{/if}
										{#if reason}<span class="text-[11px] font-normal text-warning-fg">{reason}</span>{/if}
									</span>
									<span class="line-clamp-1 text-xs text-muted-foreground" title={tool.description}>{tool.description}</span>
									{#if w}<span class={cn('block text-[11px]', w.tone)}>{w.text}</span>{/if}
								</span>
							</button>
						</li>
					{/each}
				</ul>
			</section>
		{/each}
	</div>
	{#if selected.length === 0}
		<p class="flex items-center gap-1.5 text-xs text-muted-foreground"><Minus class="size-3" />No tools selected.</p>
	{/if}
</div>
