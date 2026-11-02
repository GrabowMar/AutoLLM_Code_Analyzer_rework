<script lang="ts">
	/**
	 * CodeProfileCard — what the stored app consists of: files and source LOC by
	 * layer (backend / frontend / tests / config / excluded), who wrote them
	 * (scaffold / added / modified, from the agent's workspace manifest),
	 * structure heuristics (approximate) and what is not counted as LOC.
	 *
	 * Props: `profile` (GET /jobs/{id}/code-profile/), `loading`, `error`, `onretry`.
	 */
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import SectionHeading from '$lib/components/SectionHeading.svelte';
	import FileCode from '@lucide/svelte/icons/file-code';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import type { CodeProfile } from '$lib/api/job-insights';
	import { fmtInt, fmtShare } from './format';

	interface Props {
		profile: CodeProfile | null;
		loading?: boolean;
		error?: string;
		onretry?: () => void;
	}

	let { profile, loading = false, error = '', onretry }: Props = $props();

	let showExcluded = $state(false);

	const ORIGIN_FILL: Record<string, string> = {
		added: 'var(--chart-1)',
		modified: 'var(--chart-3)',
		scaffold: 'var(--muted-foreground)',
		unknown: 'var(--chart-5)',
	};
	const ORIGIN_LABEL: Record<string, string> = {
		added: 'Added by the model',
		modified: 'Scaffold, modified',
		scaffold: 'Scaffold, unchanged',
		unknown: 'Origin unknown',
	};
	const ORIGIN_REASON: Record<string, string> = {
		no_manifest: 'This agent run predates the workspace manifest, so file origins were not recorded.',
		single_blob: 'One raw response without file paths.',
		stage_blobs: 'Only stage outputs were stored.',
		none: 'No code stored.',
	};

	const origins = $derived(profile?.origins.known ? profile.origins : null);
	const originRows = $derived(origins ? [...origins.by_origin].sort((a, b) => b.source_loc - a.source_loc) : []);
	const originLoc = $derived(originRows.reduce((n, r) => n + r.source_loc, 0));
	const structure = $derived(profile?.structure ?? null);
	const hasLayers = $derived((profile?.layers.length ?? 0) > 0);
	// non_source_files is capped server-side; the layer row has the full count.
	const configFiles = $derived(profile?.layers.find((l) => l.layer === 'config')?.files ?? profile?.non_source_files.length ?? 0);
</script>

<Card.Root>
	<Card.Header class="pb-2">
		<Card.Title class="flex items-center gap-1.5 text-sm"><FileCode class="size-4 text-muted-foreground" aria-hidden="true" />Code profile</Card.Title>
		<Card.Description>
			Stored files by layer, who wrote them, and what is left out of source LOC (the per-KLOC denominator of the study
			tables).
		</Card.Description>
	</Card.Header>
	<Card.Content class="space-y-5 text-sm">
		{#if loading && !profile}
			<LoadingState label="loading code profile" class="py-6" />
		{:else if error && !profile}
			<ErrorAlert message={error} {onretry} />
		{:else if !profile || !hasLayers}
			<EmptyState
				compact
				icon={FileCode}
				title="No file map stored"
				description={profile?.notes[0] ?? 'This job stored no per-file code to profile.'}
			/>
		{:else}
			<div class="grid gap-5 lg:grid-cols-2">
				<!-- Layers -->
				<div class="min-w-0 space-y-2">
					<SectionHeading size="sm" level={3} title="By layer" count="{profile.all_files} files" />
					<div class="overflow-x-auto">
						<table class="text-xs">
							<thead>
								<tr>
									<th>Layer</th>
									<th class="num">Files</th>
									<th class="num" title={profile.definitions.source_loc}>Source LOC</th>
									<th class="num" title={profile.definitions.all_lines}>All lines</th>
								</tr>
							</thead>
							<tbody>
								{#each profile.layers as row (row.layer)}
									<tr class={row.files === 0 ? 'text-muted-foreground' : ''}>
										<td>{row.label}</td>
										<td class="num">{fmtInt(row.files)}</td>
										<td class="num">{#if row.source_loc == null}<span class="text-muted-foreground">—</span>{:else}{fmtInt(row.source_loc)}{/if}</td>
										<td class="num text-muted-foreground">{fmtInt(row.all_lines)}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
					<p class="text-[11px] text-muted-foreground">
						Backend + frontend source = {fmtInt(profile.loc.total_loc)} LOC (study denominator); tests
						{profile.loc.test_loc == null ? 'cannot be told apart' : `${fmtInt(profile.loc.test_loc)} LOC in ${fmtInt(profile.loc.test_files)} files, counted apart`}.
					</p>
				</div>

				<!-- Origins -->
				<div class="min-w-0 space-y-2">
					<SectionHeading size="sm" level={3} title="Who wrote it" />
					{#if origins}
						<div class="flex flex-wrap gap-1">
							{#each Object.entries(origins.counts) as [origin, n] (origin)}
								<Badge variant="outline" class="font-mono text-[11px]">{origin} {n}</Badge>
							{/each}
						</div>
						{#if originLoc > 0}
							<div class="flex h-2 w-full gap-px overflow-hidden rounded-full bg-muted" role="img" aria-label="Source LOC by origin: {originRows.map((r) => `${r.origin} ${r.source_loc}`).join(', ')}">
								{#each originRows as row (row.origin)}
									<span class="h-full" style:width="{(row.source_loc / originLoc) * 100}%" style:background-color={ORIGIN_FILL[row.origin] ?? 'var(--chart-4)'} title="{ORIGIN_LABEL[row.origin] ?? row.origin}: {row.source_loc} source LOC"></span>
								{/each}
							</div>
						{/if}
						<ul class="space-y-1 text-xs">
							{#each originRows as row (row.origin)}
								<li class="flex items-center gap-2">
									<span class="size-2 shrink-0 rounded-sm" style:background-color={ORIGIN_FILL[row.origin] ?? 'var(--chart-4)'} aria-hidden="true"></span>
									<span>{ORIGIN_LABEL[row.origin] ?? row.origin}</span>
									<span class="ml-auto font-mono tabular-nums text-muted-foreground" title="Source files">{row.source_files} src</span>
									<span class="w-16 text-right font-mono tabular-nums">{fmtInt(row.source_loc)}</span>
									<span class="w-10 text-right font-mono tabular-nums text-muted-foreground">{fmtShare(row.source_loc, originLoc)}</span>
								</li>
							{/each}
						</ul>
						<p class="text-[11px] text-muted-foreground">
							Source LOC by file origin (workspace manifest). Counts above cover every stored file.
						</p>
						{#if origins.deleted_scaffold_files.length}
							<p class="text-xs"><span class="text-muted-foreground">Deleted scaffold files:</span> <span class="font-mono">{origins.deleted_scaffold_files.join(', ')}</span></p>
						{/if}
						{#if origins.protected_modified.length}
							<p class="text-xs text-warning-fg">Protected files modified: <span class="font-mono">{origins.protected_modified.join(', ')}</span></p>
						{/if}
						{#if origins.skipped.length}
							<p class="text-xs text-muted-foreground">
								Not stored: {origins.skipped.map((s) => `${s.path} (${(s.reason ?? '').replace(/_/g, ' ')})`).join(', ')}
							</p>
						{/if}
					{:else if !profile.origins.known && profile.origins.reason === 'not_agent'}
						<p class="text-xs text-muted-foreground">
							Every stored file was generated by the model in this run's stages; the stack scaffold is merged in at build
							time and not stored with the app.
						</p>
					{:else}
						<p class="text-xs text-muted-foreground">
							Origin unknown. {ORIGIN_REASON[profile.origins.known ? '' : profile.origins.reason] ?? ''}
						</p>
					{/if}
				</div>
			</div>

			{#if structure}
				<div class="space-y-2">
					<SectionHeading size="sm" level={3} title="Structure (approximate)" />
					<dl class="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs sm:grid-cols-5">
						<div>
							<dt class="text-[11px] text-muted-foreground" title="SQLAlchemy classes deriving from db.Model">DB models</dt>
							<dd class="font-mono tabular-nums">≈ {structure.models}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground" title="@x.route / @x.get / @x.post / … decorators in backend Python">Route decorators</dt>
							<dd class="font-mono tabular-nums">≈ {structure.route_decorators}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground" title="Capitalised function / arrow components in .jsx/.tsx, one per .vue/.svelte file">Component definitions</dt>
							<dd class="font-mono tabular-nums">≈ {structure.component_definitions}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground" title="Frontend source files under a components/ directory">Component files</dt>
							<dd class="font-mono tabular-nums">{structure.frontend_component_files}</dd>
						</div>
						<div>
							<dt class="text-[11px] text-muted-foreground" title="Frontend source files under pages/, views/, routes/ or screens/">Page files</dt>
							<dd class="font-mono tabular-nums">{structure.frontend_page_files}</dd>
						</div>
					</dl>
					{#if structure.model_names.length}
						<p class="text-[11px] text-muted-foreground">Models: <span class="font-mono">{structure.model_names.join(', ')}</span></p>
					{/if}
					<p class="text-[11px] text-muted-foreground">
						Static regex counts over source files: a description of the code's shape, not a functional measure (the
						conformance suite is that).
					</p>
				</div>
			{/if}

			{#if profile.excluded.length || profile.non_source_files.length}
				<div class="space-y-1.5">
					<button
						type="button"
						class="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
						aria-expanded={showExcluded}
						onclick={() => (showExcluded = !showExcluded)}
					>
						<ChevronRight class="h-3.5 w-3.5 transition-transform duration-150 {showExcluded ? 'rotate-90' : ''}" />
						Not counted as source ({profile.excluded.reduce((n, g) => n + g.files, 0)} excluded, {configFiles} config / manifest / docs files)
					</button>
					{#if showExcluded}
						<div class="grid gap-3 sm:grid-cols-2">
							{#if profile.excluded.length}
								<ul class="space-y-1 text-xs">
									{#each profile.excluded as group (group.root)}
										<li class="rounded border px-2 py-1">
											<span class="font-mono">{group.root}/</span>
											<span class="text-muted-foreground"> · {group.files} files · {fmtInt(group.all_lines)} lines</span>
											<span class="block truncate font-mono text-[11px] text-muted-foreground" title={group.sample_paths.join('\n')}>{group.sample_paths.slice(0, 2).join(', ')}{group.files > 2 ? ', …' : ''}</span>
										</li>
									{/each}
								</ul>
							{/if}
							{#if profile.non_source_files.length}
								<ul class="space-y-0.5 text-xs">
									{#each profile.non_source_files as f (f.path)}
										<li class="flex items-center gap-2">
											<span class="min-w-0 truncate font-mono" title={f.path}>{f.path}</span>
											<Badge variant="outline" class="text-[11px]">{f.kind}</Badge>
											<span class="ml-auto font-mono tabular-nums text-muted-foreground">{fmtInt(f.all_lines)}</span>
										</li>
									{/each}
								</ul>
							{/if}
						</div>
					{/if}
				</div>
			{/if}

			{#if profile.notes.length}
				<ul class="space-y-0.5 text-[11px] text-muted-foreground">
					{#each profile.notes as note (note)}<li>{note}</li>{/each}
				</ul>
			{/if}
		{/if}
	</Card.Content>
</Card.Root>
