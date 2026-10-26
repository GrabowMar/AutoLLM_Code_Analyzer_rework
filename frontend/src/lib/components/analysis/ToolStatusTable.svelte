<script lang="ts">
	/**
	 * ToolStatusTable — every tool result of a run: status and failure reason,
	 * findings, coverage, exit code, duration, verified version, and an
	 * expandable diagnostics row (error, stderr tail, argv, limits, coverage
	 * details, config, metrics, artifact downloads).
	 *
	 * Props:
	 * - `results`: the run's tool results (plan order, AI reviewer last).
	 * - `onretry(slug)`: adds a Retry action to tools without data (terminal runs).
	 * - `retrying`: slug being retried (spinner).
	 * - `onai()`: link from the AI reviewer row to the AI review tab.
	 */
	import type { ToolResult } from '$lib/api/analysis';
	import { artifactUrl, isToolOk } from '$lib/api/analysis';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { Button } from '$lib/components/ui/button';
	import { cn } from '$lib/utils';
	import Hint from './Hint.svelte';
	import NaValue from './NaValue.svelte';
	import { failureReasonLabel, fmtBytes, fmtDateTime, fmtSeconds, shortHash } from './run-utils';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Download from '@lucide/svelte/icons/download';
	import ExternalLink from '@lucide/svelte/icons/external-link';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Sparkles from '@lucide/svelte/icons/sparkles';

	interface Props {
		results: ToolResult[];
		onretry?: (slug: string) => void;
		retrying?: string | null;
		onai?: () => void;
		class?: string;
	}

	let { results, onretry, retrying = null, onai, class: className }: Props = $props();

	let expanded = $state<Set<string>>(new Set());

	function toggle(id: string) {
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}

	const allOpen = $derived(results.length > 0 && results.every((r) => expanded.has(r.id)));

	function toggleAll() {
		expanded = allOpen ? new Set() : new Set(results.map((r) => r.id));
	}

	function isPending(r: ToolResult): boolean {
		return r.status === 'pending' || r.status === 'running';
	}

	function noDataReason(r: ToolResult): string {
		if (isPending(r)) return 'The tool has not finished yet';
		const why = failureReasonLabel(r.failure_reason) || r.status.replace(/_/g, ' ');
		return `The tool did not succeed (${why}); its findings are missing`;
	}

	function quote(arg: string): string {
		return /^[\w@%+=:,./-]+$/.test(arg) ? arg : `'${arg.replace(/'/g, `'\\''`)}'`;
	}

	function unparseable(r: ToolResult): { path: string; reason: string }[] {
		return (r.coverage?.unparseable ?? []).map((u) =>
			typeof u === 'string' ? { path: u, reason: '' } : { path: u.path, reason: u.reason ?? '' }
		);
	}

	function json(value: unknown): string {
		return JSON.stringify(value, null, 2);
	}

	function hasKeys(value: unknown): boolean {
		return !!value && typeof value === 'object' && Object.keys(value as object).length > 0;
	}

	const thClass = 'whitespace-nowrap';
</script>

<div class={cn('table-scroll-wrapper', className)}>
	<table class="min-w-full">
		<thead>
			<tr>
				<th class="w-8 pr-0">
					<button
						type="button"
						class="inline-flex size-6 cursor-pointer items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
						onclick={toggleAll}
						aria-label={allOpen ? 'Collapse all diagnostics' : 'Expand all diagnostics'}
						aria-expanded={allOpen}
					>
						<ChevronRight
							class={cn('size-3.5 transition-transform duration-200', allOpen && 'rotate-90')}
							aria-hidden="true"
						/>
					</button>
				</th>
				<th class={thClass}>Tool</th>
				<th class={thClass}>Status</th>
				<th class={cn(thClass, 'text-right')}>Findings</th>
				<th class={cn(thClass, 'hidden md:table-cell')}>Coverage</th>
				<th class={cn(thClass, 'hidden text-right lg:table-cell')}>Exit</th>
				<th class={cn(thClass, 'hidden text-right md:table-cell')}>Duration</th>
				<th class={cn(thClass, 'hidden lg:table-cell')}>Version</th>
				<th class={cn(thClass, 'text-right')}><span class="sr-only">Actions</span></th>
			</tr>
		</thead>
		<tbody>
			{#each results as r (r.id)}
				{@const open = expanded.has(r.id)}
				{@const ok = isToolOk(r.status)}
				{@const isAi = r.tool_kind === 'ai'}
				<tr class={cn(!ok && !isPending(r) && 'bg-warning/5')}>
					<td class="w-8 pr-0">
						<button
							type="button"
							class="inline-flex size-6 cursor-pointer items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
							onclick={() => toggle(r.id)}
							aria-expanded={open}
							aria-controls="diag-{r.id}"
							aria-label="{open ? 'Hide' : 'Show'} diagnostics for {r.tool_slug}"
						>
							<ChevronRight
								class={cn('size-3.5 transition-transform duration-200', open && 'rotate-90')}
								aria-hidden="true"
							/>
						</button>
					</td>
					<td>
						<div class="flex flex-wrap items-center gap-1.5">
							{#if isAi}
								<Sparkles class="size-3.5 text-accent-3" aria-hidden="true" />
							{/if}
							<span class="font-mono text-xs font-medium">{r.tool_slug}</span>
							<span
								class={cn(
									'rounded border px-1 text-[11px] leading-4',
									r.required
										? 'border-border text-foreground'
										: 'border-dashed border-border text-muted-foreground'
								)}>{r.required ? 'required' : 'optional'}</span
							>
						</div>
						<div class="text-[11px] text-muted-foreground">
							{r.tool_kind} · {r.category.replace(/_/g, ' ')}
						</div>
					</td>
					<td>
						<div class="flex flex-col items-start gap-0.5">
							<StatusBadge status={r.status} />
							{#if r.failure_reason && failureReasonLabel(r.failure_reason) !== r.status.replace(/_/g, ' ')}
								<span class="text-[11px] text-danger-fg"
									>{failureReasonLabel(r.failure_reason)}</span
								>
							{/if}
						</div>
					</td>
					<td class="text-right tabular-nums">
						{#if !ok}
							<NaValue reason={noDataReason(r)} />
						{:else if r.status === 'not_applicable'}
							<Hint
								text="Not applicable: no file in scope for this tool. This is not a scan with zero findings."
							>
								<span class="text-xs text-muted-foreground">n/a</span>
							</Hint>
						{:else if isAi}
							<Hint
								text="Counted consensus findings (grounded, reported by enough samples) of {r
									.findings.total} raw sample findings"
								underline={false}
							>
								<span class="font-medium">{r.findings.counted}</span>
								<span class="text-[11px] text-muted-foreground">consensus</span>
							</Hint>
						{:else if r.findings.counted !== r.findings.total}
							<Hint
								text="{r.findings.counted} counted toward the measurement; {r.findings.total -
									r.findings.counted} outside the measured scope or in uncounted categories"
								underline={false}
							>
								<span class="font-medium">{r.findings.counted}</span>
								<span class="text-[11px] text-muted-foreground">/ {r.findings.total}</span>
							</Hint>
						{:else}
							<span class="font-medium">{r.findings.total}</span>
						{/if}
					</td>
					<td class="hidden whitespace-nowrap text-xs md:table-cell">
						{#if isAi}
							{#if typeof r.summary.samples === 'number'}
								<span class="tabular-nums">{r.summary.samples_ok ?? 0}/{r.summary.samples}</span>
								<span class="text-muted-foreground">samples ok</span>
							{:else}
								<span class="text-muted-foreground">—</span>
							{/if}
						{:else if r.status === 'not_applicable'}
							<span class="text-muted-foreground">0 files in scope</span>
						{:else if ok && r.coverage?.files_scanned != null}
							<span class="tabular-nums"
								>{r.coverage.files_scanned}/{r.coverage.files_in_scope ??
									r.coverage.files_selected ??
									'?'}</span
							>
							<span class="text-muted-foreground">files</span>
							{#if unparseable(r).length > 0}
								<span class="ml-1 text-warning-fg">· {unparseable(r).length} unparseable</span>
							{/if}
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					</td>
					<td class="hidden text-right font-mono text-xs tabular-nums lg:table-cell">
						{r.exit_code ?? '—'}
					</td>
					<td class="hidden text-right text-xs tabular-nums md:table-cell">
						{fmtSeconds(r.duration_seconds)}
					</td>
					<td
						class="hidden max-w-48 truncate font-mono text-xs lg:table-cell"
						title={r.tool_version}
					>
						{r.tool_version || '—'}
					</td>
					<td class="whitespace-nowrap text-right">
						{#if onretry && !ok && !isPending(r)}
							<Button
								variant="ghost"
								size="sm"
								class="hidden h-7 px-2 sm:inline-flex"
								disabled={!!retrying}
								onclick={() => onretry(r.tool_slug)}
								aria-label="Retry {r.tool_slug}"
							>
								{#if retrying === r.tool_slug}
									<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />
								{:else}
									<RotateCcw class="size-3.5" />
								{/if}
								<span class="hidden sm:inline">Retry</span>
							</Button>
						{/if}
						{#if isAi && onai}
							<Button variant="ghost" size="sm" class="h-7 px-2" onclick={onai}>Review</Button>
						{/if}
					</td>
				</tr>
				{#if open}
					<tr id="diag-{r.id}" class="hover:bg-transparent">
						<td colspan="9" class="bg-muted/20 px-3 py-3 sm:px-4">
							<div class="grid gap-4 text-xs lg:grid-cols-2">
								<div class="space-y-3">
									{#if r.error_message}
										<div>
											<p class="section-label">{ok ? 'Message' : 'Error'}</p>
											<p class={cn('mt-1 break-words', !ok && 'text-danger-fg')}>
												{r.error_message}
											</p>
										</div>
									{/if}
									{#if r.diagnostics?.stderr_tail}
										<div>
											<p class="section-label">
												stderr (tail{r.diagnostics.stderr_truncated ? ', truncated' : ''})
											</p>
											<pre class="terminal mt-1 max-h-48 whitespace-pre-wrap break-words">{r
													.diagnostics.stderr_tail}</pre>
										</div>
									{/if}
									{#if r.argv?.length}
										<div>
											<p class="section-label">Command</p>
											<pre class="terminal mt-1 max-h-40 whitespace-pre-wrap break-all">{r.argv
													.map(quote)
													.join(' ')}</pre>
										</div>
									{/if}
									{#if isAi && Array.isArray(r.diagnostics?.samples)}
										<div>
											<p class="section-label">Review samples</p>
											<ul class="mt-1 flex flex-wrap gap-1.5">
												{#each r.diagnostics.samples as s (s.index)}
													<li class="flex items-center gap-1">
														<span class="font-mono text-muted-foreground">#{s.index}</span>
														<StatusBadge status={s.status} />
													</li>
												{/each}
											</ul>
										</div>
									{/if}
									{#if onretry && !ok && !isPending(r)}
										<Button
											variant="outline"
											size="sm"
											class="sm:hidden"
											disabled={!!retrying}
											onclick={() => onretry(r.tool_slug)}
										>
											<RotateCcw class="size-3.5" />Retry {r.tool_slug}
										</Button>
									{/if}
									<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
										<dt class="text-muted-foreground">Started</dt>
										<dd>{fmtDateTime(r.started_at)}</dd>
										<dt class="text-muted-foreground">Finished</dt>
										<dd>{fmtDateTime(r.finished_at)}</dd>
										<dt class="text-muted-foreground">Attempt</dt>
										<dd class="tabular-nums">{r.attempt}</dd>
										{#if r.diagnostics?.mem_limit || r.diagnostics?.timeout_s}
											<dt class="text-muted-foreground">Limits</dt>
											<dd>
												{r.diagnostics.mem_limit ?? '—'} memory · {r.diagnostics.timeout_s ?? '—'} s timeout
											</dd>
										{/if}
										{#if r.diagnostics?.timed_out || r.diagnostics?.oom_killed || r.diagnostics?.infra_error}
											<dt class="text-muted-foreground">Killed</dt>
											<dd class="text-danger-fg">
												{[
													r.diagnostics.timed_out ? 'timed out' : '',
													r.diagnostics.oom_killed ? 'OOM killer' : '',
													r.diagnostics.infra_error
														? `infrastructure: ${r.diagnostics.infra_error}`
														: ''
												]
													.filter(Boolean)
													.join(' · ')}
											</dd>
										{/if}
										{#if r.image_ref}
											<dt class="text-muted-foreground">Image</dt>
											<dd class="break-all font-mono">{r.image_ref}</dd>
										{/if}
										<dt class="text-muted-foreground">Version</dt>
										<dd class="break-all font-mono">{r.tool_version || '—'}</dd>
										<dt class="text-muted-foreground">Config hash</dt>
										<dd class="font-mono" title={r.config_hash}>
											{shortHash(r.config_hash, 16)}
										</dd>
									</dl>
								</div>

								<div class="space-y-3">
									{#if !isAi && ok && r.status !== 'not_applicable'}
										<div>
											<p class="section-label">Coverage</p>
											<p class="mt-1">
												{r.coverage?.files_scanned ?? '—'} scanned of {r.coverage?.files_in_scope ??
													'—'} in scope ({r.coverage?.files_selected ?? '—'} selected){#if (r.coverage?.skipped?.length ?? 0) > 0}{' · '}{r
														.coverage.skipped?.length} skipped{/if}{#if (r.coverage?.partial?.length ?? 0) > 0}{' · '}{r
														.coverage.partial?.length} partially scanned{/if}
											</p>
											{#if unparseable(r).length > 0}
												<ul class="mt-1 space-y-0.5 text-warning-fg">
													{#each unparseable(r) as u (u.path)}
														<li>
															<span class="font-mono">{u.path}</span>{#if u.reason}: {u.reason}{/if}
														</li>
													{/each}
												</ul>
											{/if}
										</div>
									{/if}
									{#if (r.diagnostics?.notes ?? []).length > 0}
										<div>
											<p class="section-label">Notes</p>
											<ul class="mt-1 list-disc space-y-0.5 pl-4">
												{#each r.diagnostics.notes ?? [] as note, i (i)}
													<li>{note}</li>
												{/each}
											</ul>
										</div>
									{/if}
									<div>
										<p class="section-label">Artifacts</p>
										{#if r.artifacts.length === 0}
											<p class="mt-1 text-muted-foreground">No raw output was stored.</p>
										{:else}
											<ul class="mt-1 space-y-1">
												{#each r.artifacts as a (a.name)}
													<li class="flex flex-wrap items-center gap-x-2 gap-y-0.5">
														<span class="min-w-0 break-all font-mono">{a.name}</span>
														<span class="text-muted-foreground tabular-nums"
															>{fmtBytes(a.bytes)}</span
														>
														<span class="font-mono text-muted-foreground" title="sha256 {a.sha256}"
															>{shortHash(a.sha256, 10)}</span
														>
														<span class="ml-auto flex gap-1">
															<a
																href={artifactUrl(a)}
																target="_blank"
																rel="noopener"
																class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-primary hover:bg-primary/10"
															>
																<ExternalLink class="size-3" aria-hidden="true" />View<span
																	class="sr-only"
																>
																	{a.name}</span
																>
															</a>
															<a
																href={artifactUrl(a, { download: true })}
																download
																class="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-primary hover:bg-primary/10"
															>
																<Download class="size-3" aria-hidden="true" />Download<span
																	class="sr-only"
																>
																	{a.name}</span
																>
															</a>
														</span>
													</li>
												{/each}
											</ul>
										{/if}
									</div>
									{#if hasKeys(r.config)}
										<details class="group">
											<summary
												class="cursor-pointer select-none font-medium text-muted-foreground hover:text-foreground"
											>
												Effective config
											</summary>
											<pre class="terminal mt-1 max-h-64">{json(r.config)}</pre>
										</details>
									{/if}
									{#if hasKeys(r.metrics)}
										<details class="group">
											<summary
												class="cursor-pointer select-none font-medium text-muted-foreground hover:text-foreground"
											>
												Raw metrics
											</summary>
											<pre class="terminal mt-1 max-h-64">{json(r.metrics)}</pre>
										</details>
									{/if}
								</div>
							</div>
						</td>
					</tr>
				{/if}
			{/each}
		</tbody>
	</table>
</div>
