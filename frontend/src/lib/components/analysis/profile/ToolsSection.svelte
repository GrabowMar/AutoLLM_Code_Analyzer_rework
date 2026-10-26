<script lang="ts">
	/**
	 * ToolsSection — the profile's static and metrics tools, grouped by
	 * category: enable, required, and a schema-driven options drawer.
	 *
	 * Props:
	 * - `tools`: the catalog (`/tools/` or `/profiles/defaults/`); the AI
	 *   reviewer is configured elsewhere and skipped here.
	 * - `config`: the full profile config; only `config.tools` changes here.
	 * - `onchange(config)`: the next config after each edit (tools are kept in
	 *   catalog display order, which is also the order validation reports in).
	 * - `errors`: config-relative field errors (`tools.3.config.select`).
	 * - `readonly`, `idPrefix`.
	 */
	import type {
		AnalysisTool,
		ProfileConfig,
		ProfileToolEntry,
		ToolConfigValues
	} from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Switch } from '$lib/components/ui/switch';
	import * as Sheet from '$lib/components/ui/sheet';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import ToolConfigEditor from './ToolConfigEditor.svelte';
	import {
		TOOL_CATEGORY_INFO,
		categoryLabel,
		cloneJson,
		modifiedOptions,
		toolErrorsBySlug,
		toolIcon
	} from './profile-utils';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import ListChecks from '@lucide/svelte/icons/list-checks';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import X from '@lucide/svelte/icons/x';

	interface Props {
		tools: AnalysisTool[];
		config: ProfileConfig;
		onchange?: (config: ProfileConfig) => void;
		errors?: Record<string, string>;
		readonly?: boolean;
		idPrefix?: string;
	}

	let {
		tools,
		config,
		onchange,
		errors = {},
		readonly = false,
		idPrefix = 'profile-tools'
	}: Props = $props();

	const catalog = $derived(
		tools
			.filter((t) => t.kind !== 'ai')
			.sort((a, b) => a.display_order - b.display_order || a.name.localeCompare(b.name))
	);
	const bySlug = $derived(new Map(catalog.map((t) => [t.slug, t])));
	const entries = $derived(new Map(config.tools.map((e) => [e.slug, e])));
	const unknownEntries = $derived(config.tools.filter((e) => !bySlug.has(e.slug)));
	const toolErrors = $derived(toolErrorsBySlug(errors, config));
	const rootError = $derived(errors['tools'] ?? '');

	const groups = $derived.by(() => {
		const map = new Map<string, AnalysisTool[]>();
		for (const tool of catalog) {
			const list = map.get(tool.category);
			if (list) list.push(tool);
			else map.set(tool.category, [tool]);
		}
		return [...map.entries()].map(([category, list]) => ({
			category,
			tools: list,
			enabled: list.filter((t) => entries.has(t.slug)).length
		}));
	});

	const enabledCount = $derived(config.tools.filter((e) => bySlug.has(e.slug)).length);
	const requiredCount = $derived(
		config.tools.filter((e) => e.required && bySlug.has(e.slug)).length
	);

	// Settings of a tool switched off this session, restored when it is switched back on.
	const parked = new Map<string, ProfileToolEntry>();

	function ordered(list: ProfileToolEntry[]): ProfileToolEntry[] {
		const order = (slug: string) => bySlug.get(slug)?.display_order ?? Number.MAX_SAFE_INTEGER;
		return [...list].sort((a, b) => order(a.slug) - order(b.slug) || a.slug.localeCompare(b.slug));
	}

	function emitTools(list: ProfileToolEntry[]) {
		onchange?.({ ...config, tools: ordered(list) });
	}

	function setEnabled(tool: AnalysisTool, on: boolean) {
		if (readonly) return;
		if (on) {
			if (entries.has(tool.slug)) return;
			const entry = parked.get(tool.slug) ?? {
				slug: tool.slug,
				required: tool.default_required,
				config: cloneJson(tool.default_config)
			};
			emitTools([...config.tools, entry]);
		} else {
			const entry = entries.get(tool.slug);
			if (!entry) return;
			parked.set(tool.slug, entry);
			emitTools(config.tools.filter((e) => e.slug !== tool.slug));
		}
	}

	function patchEntry(slug: string, patch: Partial<ProfileToolEntry>) {
		emitTools(config.tools.map((e) => (e.slug === slug ? { ...e, ...patch } : e)));
	}

	function removeUnknown(slug: string) {
		emitTools(config.tools.filter((e) => e.slug !== slug));
	}

	function useCatalogDefaults() {
		emitTools(
			catalog
				.filter((t) => t.default_enabled)
				.map((t) => ({
					slug: t.slug,
					required: t.default_required,
					config: cloneJson(t.default_config)
				}))
		);
	}

	function enableAll() {
		emitTools(
			catalog.map(
				(t) =>
					entries.get(t.slug) ??
					parked.get(t.slug) ?? {
						slug: t.slug,
						required: t.default_required,
						config: cloneJson(t.default_config)
					}
			)
		);
	}

	function languagesText(tool: AnalysisTool): string {
		if (tool.languages.includes('*')) return 'any text file';
		const shown = tool.languages.slice(0, 4).join(', ');
		return tool.languages.length > 4 ? `${shown} +${tool.languages.length - 4}` : shown;
	}

	// ── options drawer ─────────────────────────────────────────────────
	let drawerSlug = $state<string | null>(null);
	let drawerOpen = $state(false);
	const drawerTool = $derived(drawerSlug ? (bySlug.get(drawerSlug) ?? null) : null);
	const drawerEntry = $derived(drawerSlug ? (entries.get(drawerSlug) ?? null) : null);

	function openDrawer(slug: string) {
		drawerSlug = slug;
		drawerOpen = true;
	}

	function onDrawerConfig(config: ToolConfigValues) {
		if (drawerSlug) patchEntry(drawerSlug, { config });
	}
</script>

<div class="space-y-4" data-slot="profile-tools">
	<div class="flex flex-wrap items-center justify-between gap-2">
		<p class="text-sm text-muted-foreground" aria-live="polite">
			<span class="font-medium text-foreground tabular-nums">{enabledCount}</span> of {catalog.length}
			tools ·
			<span class="font-medium text-foreground tabular-nums">{requiredCount}</span> required
		</p>
		{#if !readonly}
			<div class="flex flex-wrap gap-1.5">
				<Button
					variant="outline"
					size="sm"
					onclick={useCatalogDefaults}
					title="Default tool selection, required flags and options"
				>
					Catalog defaults
				</Button>
				<Button
					variant="outline"
					size="sm"
					onclick={enableAll}
					disabled={enabledCount === catalog.length}
				>
					<ListChecks />Enable all
				</Button>
			</div>
		{/if}
	</div>

	<p class="text-xs leading-relaxed text-muted-foreground">
		A run is complete only when every <span class="font-medium text-foreground">required</span> tool succeeds
		or has nothing in scope; a failed required tool makes the run partial and its measurements missing
		(never zero). Optional tools are reported but never block completeness.
	</p>

	{#if rootError || toolErrors.general.length > 0}
		<div
			class="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-danger-fg"
			role="alert"
		>
			<CircleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
			<div class="space-y-0.5">
				{#if rootError}<p>{rootError}</p>{/if}
				{#each toolErrors.general as message, i (i)}<p>{message}</p>{/each}
			</div>
		</div>
	{/if}

	{#if unknownEntries.length > 0}
		<div class="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm" role="alert">
			<p class="flex items-center gap-1.5 font-medium text-warning-fg">
				<TriangleAlert class="size-4 shrink-0" aria-hidden="true" />
				Tools missing from the catalog
			</p>
			<p class="mt-0.5 text-xs text-muted-foreground">
				This profile names tools the current catalog does not have; validation rejects them.
			</p>
			<ul class="mt-2 flex flex-wrap gap-1.5">
				{#each unknownEntries as entry (entry.slug)}
					<li
						class="inline-flex items-center gap-1 rounded-full border border-border bg-background py-0.5 pl-2 pr-1 font-mono text-xs"
					>
						{entry.slug}
						{#if !readonly}
							<button
								type="button"
								class="flex size-4 cursor-pointer items-center justify-center rounded-full text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
								aria-label="Remove {entry.slug}"
								onclick={() => removeUnknown(entry.slug)}
							>
								<X class="size-3" />
							</button>
						{/if}
					</li>
				{/each}
			</ul>
		</div>
	{/if}

	<div class="space-y-4">
		{#each groups as group (group.category)}
			{@const headingId = `${idPrefix}-${group.category}`}
			<section
				class="overflow-hidden rounded-md border border-border bg-card"
				aria-labelledby={headingId}
			>
				<header
					class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 border-b border-border bg-muted/40 px-3 py-2"
				>
					<h4 id={headingId} class="text-sm font-semibold">{categoryLabel(group.category)}</h4>
					<span class="text-[11px] tabular-nums text-muted-foreground"
						>{group.enabled}/{group.tools.length} enabled</span
					>
					<p class="w-full text-[11px] leading-4 text-muted-foreground">
						{TOOL_CATEGORY_INFO[group.category as keyof typeof TOOL_CATEGORY_INFO]?.description ??
							''}
					</p>
				</header>
				<ul class="divide-y divide-border/60">
					{#each group.tools as tool (tool.slug)}
						{@const entry = entries.get(tool.slug)}
						{@const on = !!entry}
						{@const Icon = toolIcon(tool.icon)}
						{@const fieldErrors = toolErrors.bySlug[tool.slug] ?? {}}
						{@const errorCount = Object.keys(fieldErrors).length}
						{@const modified = entry ? modifiedOptions(tool, entry.config).length : 0}
						{@const checkId = `${idPrefix}-${tool.slug}-enabled`}
						<li
							class={cn(
								'flex flex-col gap-2 px-3 py-2.5 transition-colors sm:flex-row sm:items-center',
								!on && 'bg-muted/20'
							)}
						>
							<div class="flex min-w-0 flex-1 items-start gap-2.5">
								<Checkbox
									id={checkId}
									class="mt-1"
									checked={on}
									disabled={readonly}
									aria-describedby="{checkId}-meta"
									onCheckedChange={(v) => setEnabled(tool, v === true)}
								/>
								<Icon
									class={cn(
										'mt-0.5 size-4 shrink-0',
										on ? 'text-foreground' : 'text-muted-foreground'
									)}
									aria-hidden="true"
								/>
								<div class="min-w-0 flex-1">
									<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
										<label
											for={checkId}
											class={cn(
												'text-sm font-medium',
												readonly ? 'cursor-default' : 'cursor-pointer',
												!on && 'text-muted-foreground'
											)}
										>
											{tool.name}
										</label>
										<span
											class="font-mono text-[11px] text-muted-foreground"
											title={tool.version_source === 'image_manifest'
												? 'Version read from the built analyzer image'
												: 'Catalog version — not verified against a built analyzer image'}
										>
											{tool.version
												? `v${tool.version}`
												: 'version —'}{#if tool.version_source !== 'image_manifest'}<span
													class="text-warning-fg">*</span
												>{/if}
										</span>
										{#if tool.canary?.verdict === 'passed'}
											<span
												class="inline-flex items-center gap-1 text-[11px] text-success-fg"
												title="Passed its self-test on this image"
											>
												<CircleCheck class="size-3" aria-hidden="true" />self-test
											</span>
										{:else if tool.canary}
											<StatusBadge
												status={tool.canary.verdict}
												label={tool.canary.verdict === 'failed'
													? 'self-test failed'
													: 'self-test unverified'}
											/>
										{:else if tool.expectations}
											<StatusBadge status="untested" label="no self-test" />
										{/if}
										{#if errorCount > 0}
											<span
												class="inline-flex items-center gap-1 text-[11px] font-medium text-danger-fg"
											>
												<CircleAlert class="size-3" aria-hidden="true" />{errorCount} problem{errorCount ===
												1
													? ''
													: 's'}
											</span>
										{/if}
									</div>
									<p id="{checkId}-meta" class="mt-0.5 text-[11px] leading-4 text-muted-foreground">
										{languagesText(tool)}
										{#if tool.kind === 'metrics'}· metrics{/if}
										{#if tool.docs_url}
											·
											<a
												href={tool.docs_url}
												target="_blank"
												rel="noopener noreferrer"
												class="inline-flex items-center gap-0.5 rounded underline-offset-2 hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
												>docs<ExternalLink class="size-3" aria-hidden="true" /><span
													class="sr-only"
												>
													(opens in a new tab)</span
												></a
											>
										{/if}
									</p>
									{#if fieldErrors['slug'] || fieldErrors['required'] || fieldErrors['']}
										<p class="mt-1 text-[11px] text-danger-fg">
											{fieldErrors['slug'] || fieldErrors['required'] || fieldErrors['']}
										</p>
									{/if}
								</div>
							</div>

							{#if on && entry}
								<div class="flex shrink-0 items-center gap-3 pl-[3.25rem] sm:pl-0">
									<label
										class={cn(
											'flex items-center gap-2 text-xs',
											readonly ? 'cursor-default' : 'cursor-pointer'
										)}
									>
										<Switch
											checked={entry.required}
											size="sm"
											label="{tool.name} required"
											disabled={readonly}
											onCheckedChange={(checked) => patchEntry(tool.slug, { required: checked })}
										/>
										<span
											class={entry.required
												? 'font-medium text-foreground'
												: 'text-muted-foreground'}>Required</span
										>
									</label>
									<Button
										variant="outline"
										size="sm"
										class={cn('h-7 px-2', errorCount > 0 && 'border-destructive/50 text-danger-fg')}
										disabled={tool.config_schema.length === 0 &&
											Object.keys(entry.config).length === 0}
										aria-label="{readonly ? 'View' : 'Configure'} {tool.name} options"
										onclick={() => openDrawer(tool.slug)}
									>
										<SlidersHorizontal />
										{#if tool.config_schema.length === 0}
											No options
										{:else if modified > 0}
											{modified} changed
										{:else}
											Options
										{/if}
									</Button>
								</div>
							{/if}
						</li>
					{/each}
				</ul>
			</section>
		{/each}
	</div>
	{#if catalog.some((t) => t.version_source !== 'image_manifest')}
		<p class="text-[11px] text-muted-foreground">
			<span class="text-warning-fg">*</span> Catalog version, not yet verified against a built analyzer
			image.
		</p>
	{/if}
</div>

<Sheet.Root bind:open={drawerOpen}>
	<Sheet.Content side="right" class="w-full gap-0 overflow-y-auto p-0 sm:max-w-lg">
		{#if drawerTool && drawerEntry}
			{@const Icon = toolIcon(drawerTool.icon)}
			<Sheet.Header class="space-y-1 border-b border-border px-5 py-4 pr-12">
				<Sheet.Title class="flex items-center gap-2">
					<Icon class="size-4 shrink-0" aria-hidden="true" />
					{drawerTool.name} options
				</Sheet.Title>
				<Sheet.Description class="text-xs">
					{drawerTool.version ? `v${drawerTool.version} · ` : ''}{categoryLabel(
						drawerTool.category
					)}{readonly ? ' · read-only' : ' · changes apply to this profile only'}
				</Sheet.Description>
			</Sheet.Header>
			<div class="space-y-4 px-5 py-4">
				<p class="text-xs leading-relaxed text-muted-foreground">{drawerTool.description}</p>
				<ToolConfigEditor
					tool={drawerTool}
					config={drawerEntry.config}
					errors={toolErrors.bySlug[drawerTool.slug] ?? {}}
					{readonly}
					idPrefix="{idPrefix}-{drawerTool.slug}-opt"
					onchange={onDrawerConfig}
				/>
			</div>
			<Sheet.Footer class="mt-auto border-t border-border px-5 py-3">
				<Button
					size="sm"
					variant={readonly ? 'outline' : 'default'}
					onclick={() => (drawerOpen = false)}
				>
					{readonly ? 'Close' : 'Done'}
				</Button>
			</Sheet.Footer>
		{/if}
	</Sheet.Content>
</Sheet.Root>
