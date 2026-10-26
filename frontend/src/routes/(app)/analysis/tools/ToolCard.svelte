<script lang="ts">
	/**
	 * ToolCard — one catalog tool: kind, category, languages, version
	 * (verified or not), self-test status, docs, options schema and canary
	 * expectations.
	 *
	 * Props: `tool` (`AnalysisTool`), `index` (entrance stagger).
	 */
	import type { AnalysisTool } from '$lib/api/analysis';
	import { cn } from '$lib/utils';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import {
		TOOL_KIND_LABELS,
		categoryLabel,
		describeOption,
		toolIcon
	} from '$lib/components/analysis/profile/profile-utils';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	interface Props {
		tool: AnalysisTool;
		index?: number;
	}

	let { tool, index = 0 }: Props = $props();

	let optionsOpen = $state(false);
	let expectationsOpen = $state(false);

	const Icon = $derived(toolIcon(tool.icon));
	const verified = $derived(tool.version_source === 'image_manifest');
	const canary = $derived.by(() => {
		if (tool.kind === 'ai') return { status: 'not_applicable', label: 'no self-test (AI)' };
		if (!tool.canary) return { status: 'untested', label: 'untested on this image' };
		if (tool.canary.verdict === 'passed') return { status: 'passed', label: 'self-test passed' };
		if (tool.canary.verdict === 'failed') return { status: 'failed', label: 'self-test failed' };
		return { status: 'unverified', label: 'self-test unverified' };
	});

	function languages(list: string[]): string {
		return list.includes('*') ? 'any text file' : list.join(', ');
	}

	function fieldMeta(field: AnalysisTool['config_schema'][number]): string {
		const parts: string[] = [field.type];
		if (field.min != null || field.max != null)
			parts.push(`${field.min ?? '…'}–${field.max ?? '…'}`);
		parts.push(field.applies_to === 'argv' ? 'command line' : 'interpretation');
		return parts.join(' · ');
	}

	const disclosureClass =
		'flex w-full cursor-pointer items-center gap-1.5 rounded py-1 text-left text-xs font-medium transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40';
</script>

<Card.Root class="rise-in flex flex-col" style="--stagger-i: {Math.min(index, 8)}">
	<Card.Header class="space-y-2 pb-3">
		<div class="flex items-start gap-3">
			<div
				class="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-muted/50"
				aria-hidden="true"
			>
				<Icon class="size-4" />
			</div>
			<div class="min-w-0 flex-1">
				<Card.Title class="flex flex-wrap items-baseline gap-x-2 text-base">
					{tool.name}
					<span class="font-mono text-[11px] font-normal text-muted-foreground">{tool.slug}</span>
				</Card.Title>
				<div class="mt-1 flex flex-wrap items-center gap-1.5">
					<Badge variant="secondary">{TOOL_KIND_LABELS[tool.kind] ?? tool.kind}</Badge>
					<Badge variant="outline">{categoryLabel(tool.category)}</Badge>
					<StatusBadge status={canary.status} label={canary.label} />
				</div>
			</div>
		</div>
	</Card.Header>
	<Card.Content class="flex flex-1 flex-col gap-3 text-sm">
		<p class="text-xs leading-relaxed text-muted-foreground">{tool.description}</p>
		<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
			<dt class="text-muted-foreground">Version</dt>
			<dd class="flex flex-wrap items-center gap-1.5">
				<span class="font-mono">{tool.version || '—'}</span>
				{#if tool.kind !== 'ai'}
					{#if verified}
						<span class="text-[11px] text-success-fg">verified from image</span>
					{:else}
						<span
							class="inline-flex items-center gap-1 text-[11px] text-warning-fg"
							title="The image is not built, so the version comes from the catalog"
						>
							<TriangleAlert class="size-3" aria-hidden="true" />unverified
						</span>
					{/if}
				{:else}
					<span class="text-[11px] text-muted-foreground"
						>model and harness are set per profile</span
					>
				{/if}
			</dd>
			<dt class="text-muted-foreground">Languages</dt>
			<dd>{languages(tool.languages)}</dd>
			<dt class="text-muted-foreground">File kinds</dt>
			<dd>{tool.file_kinds.includes('*') ? 'all' : tool.file_kinds.join(', ')}</dd>
			<dt class="text-muted-foreground">Output</dt>
			<dd>{tool.output_kind}</dd>
			<dt class="text-muted-foreground">Limits</dt>
			<dd class="tabular-nums">{tool.mem_limit} memory · {tool.timeout_s}s timeout</dd>
			<dt class="text-muted-foreground">Default</dt>
			<dd>
				{tool.default_enabled ? 'enabled' : 'off'}{tool.default_enabled
					? tool.default_required
						? ', required'
						: ', optional'
					: ''}
				{#if !tool.is_enabled}<span class="text-warning-fg"> · disabled in the catalog</span>{/if}
			</dd>
		</dl>

		<div class="mt-auto space-y-1 border-t border-border pt-2 text-muted-foreground">
			{#if tool.config_schema.length > 0}
				<button
					type="button"
					class={disclosureClass}
					aria-expanded={optionsOpen}
					aria-controls="tool-{tool.slug}-options"
					onclick={() => (optionsOpen = !optionsOpen)}
				>
					<ChevronRight
						class={cn('size-3.5 transition-transform duration-150', optionsOpen && 'rotate-90')}
						aria-hidden="true"
					/>
					Options <span class="font-normal">({tool.config_schema.length})</span>
				</button>
				{#if optionsOpen}
					<ul id="tool-{tool.slug}-options" class="space-y-2 pb-1 pl-5 text-xs">
						{#each tool.config_schema as field (field.name)}
							<li>
								<p class="text-foreground">
									{field.label}
									<span class="font-mono text-[11px] text-muted-foreground">{field.name}</span>
								</p>
								<p class="text-[11px] leading-4">{field.description}</p>
								<p class="text-[11px] leading-4">
									<span class="font-mono">{fieldMeta(field)}</span> · default
									<span class="text-foreground">{describeOption(field, field.default)}</span>
								</p>
							</li>
						{/each}
					</ul>
				{/if}
			{:else}
				<p class="py-1 text-xs">
					{tool.kind === 'ai'
						? 'Configured in each profile’s AI reviewer section.'
						: 'No tunable options.'}
				</p>
			{/if}

			{#if tool.expectations}
				<button
					type="button"
					class={disclosureClass}
					aria-expanded={expectationsOpen}
					aria-controls="tool-{tool.slug}-expectations"
					onclick={() => (expectationsOpen = !expectationsOpen)}
				>
					<ChevronRight
						class={cn(
							'size-3.5 transition-transform duration-150',
							expectationsOpen && 'rotate-90'
						)}
						aria-hidden="true"
					/>
					Self-test expectations
					<span class="font-normal">({tool.expectations.must_find.length} rules)</span>
				</button>
				{#if expectationsOpen}
					<div
						id="tool-{tool.slug}-expectations"
						class="space-y-1.5 pb-1 pl-5 text-[11px] leading-4"
					>
						<p>Must find on the vulnerable fixture:</p>
						<ul class="flex flex-wrap gap-1">
							{#each tool.expectations.must_find as entry, i (i)}
								<li
									class="rounded border border-border px-1.5 py-0.5 font-mono text-foreground"
									title={entry.path ?? 'any file'}
								>
									{entry.rule}
								</li>
							{/each}
						</ul>
						{#if tool.expectations.clean_categories.length > 0}
							<p>
								Nothing counted on the clean fixture in: {tool.expectations.clean_categories.join(
									', '
								)}.
							</p>
						{/if}
						{#if tool.expectations.min_findings != null}
							<p>At least {tool.expectations.min_findings} findings on the vulnerable fixture.</p>
						{/if}
					</div>
				{/if}
			{/if}

			{#if tool.docs_url}
				<a
					href={tool.docs_url}
					target="_blank"
					rel="noopener noreferrer"
					class="inline-flex items-center gap-1 rounded py-1 text-xs font-medium text-primary underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40"
				>
					Documentation<ExternalLink class="size-3" aria-hidden="true" /><span class="sr-only">
						(opens in a new tab)</span
					>
				</a>
			{/if}
		</div>
	</Card.Content>
</Card.Root>
