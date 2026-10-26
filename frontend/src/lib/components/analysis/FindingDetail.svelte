<script lang="ts">
	/**
	 * FindingDetail — everything a tool said about one finding: what and why
	 * (description), how to fix it (the tool's suggestion / autofix), where (code,
	 * location, duplicate), the rule (name, docs, CWE / OWASP / WSTG, references),
	 * and the domain facts parsers extract — vulnerable package and fixed versions,
	 * the HTTP request a scanner flagged, complexity — plus any other tool data.
	 * Only what the tool reported is shown; nothing is rated here.
	 *
	 * Props: `finding`, `toolName`.
	 */
	import type { RunFinding } from '$lib/api/runs';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import Copy from '@lucide/svelte/icons/copy';
	import { toast } from 'svelte-sonner';
	import LevelBadge from './LevelBadge.svelte';
	import {
		advisoryUrl,
		complexityInfo,
		cweUrl,
		dependencyInfo,
		duplicateInfo,
		httpInfo,
		isPrivateUrl,
		locationText,
		otherData,
		ruleInfo,
		safeUrl,
	} from './finding-data';

	interface Props {
		finding: RunFinding;
		toolName: string;
	}

	let { finding: f, toolName }: Props = $props();

	const rule = $derived(ruleInfo(f));
	const dep = $derived(dependencyInfo(f));
	const http = $derived(httpInfo(f));
	const dup = $derived(duplicateInfo(f));
	const cx = $derived(complexityInfo(f));
	const other = $derived(otherData(f));
	const location = $derived(locationText(f));
	const description = $derived(f.description && f.description.trim() !== f.title.trim() ? f.description.trim() : '');
	// Some tools put only a link in the description.
	const descriptionUrl = $derived(safeUrl(description));
	const isDependency = $derived(!!dep && (dep.ids.length > 0 || dep.installed !== '' || dep.fixedIn.length > 0));
	const hasMain = $derived(!!(descriptionUrl || description || f.suggestion || rule.fix || f.code_snippet || dup || http));

	async function copy(text: string, what: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success(`${what} copied`);
		} catch {
			toast.error('Copy failed');
		}
	}
</script>

{#snippet link(href: string, text: string)}
	<a {href} target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline">
		{text}<ExternalLink class="size-3 shrink-0" aria-hidden="true" />
	</a>
{/snippet}

{#snippet chipLink(id: string, href: string | null)}
	{#if href}
		<a {href} target="_blank" rel="noopener noreferrer" class="rounded border border-border bg-muted px-1.5 py-px font-mono text-[11px] text-foreground hover:border-primary/50 hover:text-primary">{id}</a>
	{:else}
		<span class="rounded border border-border bg-muted px-1.5 py-px font-mono text-[11px]">{id}</span>
	{/if}
{/snippet}

<div class={hasMain ? 'grid gap-x-6 gap-y-4 text-sm @3xl:grid-cols-[minmax(0,1fr)_minmax(16rem,22rem)]' : 'text-sm'}>
	<!-- What / how to fix / where -->
	<div class={hasMain ? 'min-w-0 space-y-3' : 'hidden'}>
		{#if descriptionUrl}
			<p>{@render link(descriptionUrl, 'Advisory')}</p>
		{:else if description}
			<p class="whitespace-pre-line text-foreground/90">{description}</p>
		{/if}

		{#if f.suggestion || rule.fix}
			<div class="rounded-md border border-success/30 bg-success/5 px-3 py-2">
				<p class="section-label mb-1">Suggested fix (from the tool)</p>
				{#if f.suggestion}<p class="whitespace-pre-line">{f.suggestion}</p>{/if}
				{#if rule.fix}<pre class="mt-1 overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-xs">{rule.fix}</pre>{/if}
			</div>
		{/if}

		{#if f.code_snippet}
			<div>
				<p class="section-label mb-1">Code{location ? ` · ${location}` : ''}</p>
				<pre class="max-h-64 overflow-auto rounded-md border border-border bg-muted/50 px-3 py-2 font-mono text-xs leading-5">{f.code_snippet}</pre>
			</div>
		{/if}

		{#if dup}
			<p class="text-xs text-muted-foreground">
				Same code also in <span class="font-mono text-foreground">{dup.file}{dup.startLine != null ? `:${dup.startLine}` : ''}{dup.endLine != null ? `–${dup.endLine}` : ''}</span>
				{#if dup.lines != null}· {dup.lines} lines{/if}
				{#if dup.tokens != null}· {dup.tokens} tokens{/if}
			</p>
		{/if}

		{#if http}
			<div class="space-y-1.5 rounded-md border border-border px-3 py-2">
				<p class="section-label">Request the scanner flagged</p>
				{#if http.url}
					<p class="break-all font-mono text-xs">
						{#if http.method}<span class="mr-1.5 font-semibold">{http.method}</span>{/if}{http.url}
						{#if !isPrivateUrl(http.url) && safeUrl(http.url)}<span class="ml-2 font-sans">{@render link(safeUrl(http.url)!, 'Open')}</span>{/if}
					</p>
				{/if}
				<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
					{#if http.param}<dt class="text-muted-foreground">Parameter</dt><dd class="font-mono">{http.param}</dd>{/if}
					{#if http.attack}<dt class="text-muted-foreground">Attack</dt><dd class="break-all font-mono">{http.attack}</dd>{/if}
					{#if http.evidence}<dt class="text-muted-foreground">Evidence</dt><dd class="break-all font-mono">{http.evidence}</dd>{/if}
					{#if http.matcher}<dt class="text-muted-foreground">Matched</dt><dd class="font-mono">{http.matcher}</dd>{/if}
					{#if http.instances != null}<dt class="text-muted-foreground">Instances</dt><dd class="font-mono tabular-nums">{http.instances}</dd>{/if}
				</dl>
				{#if http.otherUrls.length > 0}
					<details class="text-xs">
						<summary class="cursor-pointer text-muted-foreground hover:text-foreground">{http.otherUrls.length} more URL{http.otherUrls.length === 1 ? '' : 's'}</summary>
						<ul class="mt-1 max-h-40 space-y-0.5 overflow-auto font-mono">
							{#each http.otherUrls as u (u)}<li class="break-all">{u}</li>{/each}
						</ul>
					</details>
				{/if}
				{#if http.otherInfo}<p class="whitespace-pre-line text-xs text-muted-foreground">{http.otherInfo}</p>{/if}
				{#if http.curl}
					<div class="flex items-start gap-2">
						<pre class="min-w-0 flex-1 overflow-x-auto rounded bg-muted px-2 py-1 font-mono text-xs">{http.curl}</pre>
						<button type="button" class="cursor-pointer rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground" title="Copy command" aria-label="Copy reproduction command" onclick={() => copy(http.curl, 'Command')}>
							<Copy class="size-3.5" />
						</button>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	<!-- Facts -->
	<dl class="grid h-fit min-w-0 max-w-2xl grid-cols-[7rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
		<dt class="text-muted-foreground">Tool</dt>
		<dd>{toolName}</dd>

		<dt class="text-muted-foreground">Level</dt>
		<dd><LevelBadge label={f.tool_severity} fallback="Not rated by this tool" /></dd>

		{#if rule.toolConfidence}
			<dt class="text-muted-foreground">Confidence</dt>
			<dd class="font-mono">{rule.toolConfidence}</dd>
		{/if}

		{#if f.rule_id || rule.name}
			<dt class="text-muted-foreground">Rule</dt>
			<dd class="min-w-0 space-y-0.5">
				{#if f.rule_id}<span class="block break-all font-mono">{f.rule_id}{#if rule.messageId && rule.messageId !== f.rule_id}<span class="text-muted-foreground"> ({rule.messageId})</span>{/if}</span>{/if}
				{#if rule.name && rule.name !== f.rule_id}<span class="block text-muted-foreground">{rule.name}</span>{/if}
				{#if rule.docsUrl}{@render link(rule.docsUrl, 'Rule documentation')}{/if}
			</dd>
		{/if}

		{#if location && !f.code_snippet}
			<dt class="text-muted-foreground">Location</dt>
			<dd class="break-all font-mono">{location}</dd>
		{/if}

		{#if f.category}
			<dt class="text-muted-foreground">Category</dt>
			<dd>{f.category}</dd>
		{/if}

		{#if rule.cwe.length || rule.owasp.length || rule.wstg.length}
			<dt class="text-muted-foreground">Weakness</dt>
			<dd class="flex flex-wrap gap-1">
				{#each rule.cwe as id (id)}{@render chipLink(id, cweUrl(id))}{/each}
				{#each rule.owasp as id (id)}{@render chipLink(id, null)}{/each}
				{#each rule.wstg as id (id)}{@render chipLink(id, null)}{/each}
			</dd>
		{/if}

		{#if rule.likelihood || rule.impact}
			<dt class="text-muted-foreground">Likelihood / impact</dt>
			<dd class="font-mono">{rule.likelihood || '—'} / {rule.impact || '—'}</dd>
		{/if}

		{#if rule.secretType}
			<dt class="text-muted-foreground">Secret type</dt>
			<dd>{rule.secretType}{#if rule.entropy != null}<span class="text-muted-foreground"> · entropy {rule.entropy.toFixed(2)}</span>{/if}</dd>
		{/if}

		{#if dep}
			<dt class="text-muted-foreground">{isDependency ? 'Package' : 'Module'}</dt>
			<dd class="font-mono">
				{dep.package}{#if dep.installed}<span class="text-muted-foreground"> {dep.installed}</span>{/if}
				{#if dep.ecosystem}<span class="ml-1 font-sans text-muted-foreground">({dep.ecosystem})</span>{/if}
				{#if dep.direct != null}<span class="ml-1 font-sans text-muted-foreground">· {dep.direct ? 'direct' : 'transitive'}</span>{/if}
			</dd>
			{#if dep.fixedIn.length > 0}
				<dt class="text-muted-foreground">Fixed in</dt>
				<dd class="font-mono text-success-fg">{dep.fixedIn.join(', ')}</dd>
			{:else if dep.ids.length > 0}
				<dt class="text-muted-foreground">Fixed in</dt>
				<dd class="text-muted-foreground">No fixed version reported</dd>
			{/if}
			{#if dep.cvssScore != null || dep.cvssVector}
				<dt class="text-muted-foreground">CVSS</dt>
				<dd class="break-all font-mono">{dep.cvssScore ?? ''}{#if dep.cvssVector}<span class="block text-muted-foreground">{dep.cvssVector}</span>{/if}</dd>
			{/if}
			{#if dep.manifest}
				<dt class="text-muted-foreground">Manifest</dt>
				<dd class="break-all font-mono">{dep.manifest}</dd>
			{/if}
			{#if dep.ids.length > 0}
				<dt class="text-muted-foreground">Advisories</dt>
				<dd class="flex flex-wrap gap-1">
					{#each dep.ids as id (id)}{@render chipLink(id, advisoryUrl(id))}{/each}
				</dd>
			{/if}
		{/if}

		{#if cx}
			{#if cx.fn}<dt class="text-muted-foreground">Function</dt><dd class="break-all font-mono">{cx.fn}</dd>{/if}
			{#if cx.complexity != null}<dt class="text-muted-foreground">Complexity</dt><dd class="font-mono tabular-nums">{cx.complexity}{#if cx.rank}<span class="text-muted-foreground"> · rank {cx.rank}</span>{/if}</dd>{/if}
			{#if cx.nloc != null}<dt class="text-muted-foreground">Lines of code</dt><dd class="font-mono tabular-nums">{cx.nloc}</dd>{/if}
		{/if}

		{#if rule.tags.length > 0}
			<dt class="text-muted-foreground">Tags</dt>
			<dd class="flex flex-wrap gap-1">{#each rule.tags as t (t)}<span class="rounded bg-muted px-1.5 py-px text-[11px]">{t}</span>{/each}</dd>
		{/if}

		{#if rule.references.length > 0}
			<dt class="text-muted-foreground">References</dt>
			<dd class="min-w-0 space-y-0.5">
				{#each rule.references.slice(0, 8) as url (url)}
					<a href={url} target="_blank" rel="noopener noreferrer" class="block truncate text-primary underline-offset-2 hover:underline" title={url}>{url.replace(/^https?:\/\//, '')}</a>
				{/each}
				{#if rule.references.length > 8}<span class="text-muted-foreground">+{rule.references.length - 8} more</span>{/if}
			</dd>
		{/if}

		{#each other as [key, value] (key)}
			<dt class="capitalize text-muted-foreground">{key}</dt>
			<dd class="break-all font-mono">{value}</dd>
		{/each}
	</dl>
</div>
