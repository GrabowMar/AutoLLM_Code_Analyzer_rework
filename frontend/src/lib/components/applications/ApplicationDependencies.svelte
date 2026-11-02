<script lang="ts">
// Dependencies of one app: what its manifests declare next to its stack
// scaffold's (who chose each package: scaffold, model-added, re-pinned), what
// the evaluation image actually installed, and — for template runs, which
// store no manifests — the imports the generator reported.
import { tableFrame } from '$lib/components/table';
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Package from '@lucide/svelte/icons/package';
import EmptyState from '$lib/components/EmptyState.svelte';
import LoadingState from '$lib/components/LoadingState.svelte';
import ErrorAlert from '$lib/components/ErrorAlert.svelte';
import type { CodeDependencies, DependencyStatus } from '$lib/api/job-insights';

interface Props {
	/** Legacy list reported by template-mode generators (result_data.backend_dependencies). */
	deps: (string | unknown)[];
	mode: string;
	/** From the code profile; null while loading or unavailable. */
	dependencies?: CodeDependencies | null;
	loading?: boolean;
	error?: string;
	onretry?: () => void;
}

let { deps, mode, dependencies = null, loading = false, error = '', onretry }: Props = $props();

const STATUS_LABEL: Record<DependencyStatus, string> = {
	scaffold: 'scaffold',
	added: 'model-added',
	repinned: 're-pinned',
	unknown: 'unknown',
};

const RESOLVED_BY: Record<string, string> = {
	app: 'app manifest',
	scaffold: 'scaffold manifest',
	healing: 'build healing',
};

const counts = $derived(dependencies?.counts ?? null);
const declared = $derived(
	[...(dependencies?.declared ?? [])].sort(
		(a, b) => statusRank(a.status) - statusRank(b.status) || a.ecosystem.localeCompare(b.ecosystem) || a.package.localeCompare(b.package)
	)
);
const resolved = $derived(dependencies?.resolved_python ?? null);
const reported = $derived(dependencies?.reported_by_generator ?? (deps.length ? deps.map((d) => String(d)) : null));
const stack = $derived(dependencies?.scaffold_stack ?? null);
const nothing = $derived(!declared.length && !(resolved?.length) && !(reported?.length) && !(dependencies?.scaffold.length));

function statusRank(s: DependencyStatus | undefined): number {
	return s === 'added' ? 0 : s === 'repinned' ? 1 : s === 'unknown' ? 2 : 3;
}

function summary(): { label: string; value: number | null; title: string }[] {
	if (!counts) return [];
	return [
		{ label: 'declared', value: counts.declared, title: 'Packages the app\'s requirements*.txt / package.json declare' },
		{ label: 'scaffold', value: counts.scaffold, title: 'Packages the stack scaffold declares' },
		{ label: 'model-added', value: counts.model_added, title: 'Declared by the app but not by the scaffold: the model\'s own choices' },
		{ label: 're-pinned', value: counts.repinned, title: 'Scaffold packages whose version spec the model changed (a different range, not just a different spelling)' },
		{ label: 'dropped', value: counts.dropped_scaffold, title: 'Scaffold packages the app no longer declares' },
		{ label: 'resolved (pip)', value: counts.resolved_python, title: 'Python packages installed in the evaluation image (pip freeze)' },
	];
}
</script>

<section id="dependencies" class="space-y-4">
	{#if loading && !dependencies}
		<Card.Root><Card.Content><LoadingState label="loading dependencies" class="py-8" /></Card.Content></Card.Root>
	{:else if error && !dependencies && !reported?.length}
		<ErrorAlert message={error} {onretry} />
	{:else if nothing}
		<Card.Root>
			<Card.Content>
				<EmptyState compact icon={Package} title="No dependencies recorded" description="This app stores no manifest, and the generator reported no packages." />
			</Card.Content>
		</Card.Root>
	{:else}
		{#if counts}
			<Card.Root>
				<Card.Header class="pb-2">
					<Card.Title class="text-sm">Who chose the dependencies</Card.Title>
					<Card.Description>
						The app's manifests against its stack scaffold{#if stack}&nbsp;(<span class="font-mono">{stack.slug}{stack.version != null ? ` v${stack.version}` : ''}</span>{stack.source === 'latest' ? ', latest version: the pinned one is gone' : stack.source === 'missing' ? ': stack not found' : ''}){/if}.
						Same rules as the study's dependency-ownership tables.
					</Card.Description>
				</Card.Header>
				<Card.Content>
					<dl class="grid grid-cols-3 gap-3 sm:grid-cols-6">
						{#each summary() as item (item.label)}
							<div title={item.title}>
								<dt class="text-[11px] text-muted-foreground">{item.label}</dt>
								<dd class="font-mono text-lg font-medium tabular-nums">{item.value ?? '—'}</dd>
							</div>
						{/each}
					</dl>
					{#if !dependencies?.has_manifests}
						<p class="mt-2 text-[11px] text-muted-foreground">
							{mode === 'agent' ? 'This run stored no manifest' : 'Template runs store only the generated code; the image is built with the scaffold\'s manifests'}: model-added and re-pinned packages are unknown.
						</p>
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}

		{#if declared.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="flex items-center gap-2 text-sm font-medium">Declared by the app <Badge variant="outline" class="text-xs">{declared.length}</Badge></Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead>
								<tr>
									<th>Package</th>
									<th>Ecosystem</th>
									<th>Declared spec</th>
									<th>Scaffold spec</th>
									<th>Chosen by</th>
								</tr>
							</thead>
							<tbody>
								{#each declared as dep (dep.ecosystem + dep.package + dep.manifest)}
									<tr>
										<td class="font-mono text-xs">{dep.package}</td>
										<td class="text-xs text-muted-foreground">{dep.ecosystem}</td>
										<td class="font-mono text-xs">{dep.spec || 'any'}</td>
										<td class="font-mono text-xs">{#if dep.scaffold_spec != null}{dep.scaffold_spec || 'any'}{:else}<span class="text-muted-foreground">—</span>{/if}</td>
										<td>
											<Badge variant={dep.status === 'scaffold' ? 'secondary' : 'outline'} class="text-[11px]">{STATUS_LABEL[dep.status ?? 'unknown']}</Badge>
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}

		{#if dependencies?.dropped_scaffold?.length}
			<p class="text-xs"><span class="text-muted-foreground">Scaffold packages no longer declared:</span> <span class="font-mono">{dependencies.dropped_scaffold.join(', ')}</span></p>
		{/if}

		{#if resolved?.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="flex items-center gap-2 text-sm font-medium">Installed in the evaluation image (Python) <Badge variant="outline" class="text-xs">{resolved.length}</Badge></Card.Title>
					<Card.Description>Resolved versions from the platform's build; transitive packages included.</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead><tr><th>Package</th><th>Version</th><th title="Which manifest asked for it: the app's own (agent runs), the stack scaffold's (template runs, merged in at build time) or a requirement the platform's build healing added">Declared by</th></tr></thead>
							<tbody>
								{#each resolved as dep, i (`${dep.package}:${i}`)}
									<tr>
										<td class="font-mono text-xs">{dep.package}</td>
										<td class="font-mono text-xs">{dep.version ?? '—'}</td>
										<td class="text-xs {dep.declared ? '' : 'text-muted-foreground'}">{RESOLVED_BY[dep.declared_by ?? ''] ?? (dep.declared ? 'declared' : 'transitive / tooling')}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}

		{#if !declared.length && dependencies?.scaffold.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="flex items-center gap-2 text-sm font-medium">Scaffold manifests <Badge variant="outline" class="text-xs">{dependencies.scaffold.length}</Badge></Card.Title>
					<Card.Description>What the stack scaffold declares (the image is built with these).</Card.Description>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="table-frame" {@attach tableFrame}>
						<table>
							<thead><tr><th>Package</th><th>Ecosystem</th><th>Spec</th></tr></thead>
							<tbody>
								{#each dependencies.scaffold as dep (dep.ecosystem + dep.package + dep.manifest)}
									<tr>
										<td class="font-mono text-xs">{dep.package}</td>
										<td class="text-xs text-muted-foreground">{dep.ecosystem}</td>
										<td class="font-mono text-xs">{dep.spec || 'any'}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}

		{#if reported?.length}
			<Card.Root class="gap-0 overflow-clip py-0">
				<Card.Header class="border-b border-border py-3">
					<Card.Title class="flex items-center gap-2 text-sm font-medium">{mode === 'scaffolding' ? 'Backend imports' : 'Packages'} reported by the generator <Badge variant="outline" class="text-xs">{reported.length}</Badge></Card.Title>
				</Card.Header>
				<Card.Content class="p-0">
					<div class="overflow-x-auto">
						<table>
							<thead><tr><th>Package</th><th>Version</th></tr></thead>
							<tbody>
								{#each reported as dep, i (i)}
									{@const parts = dep.split(/[=<>~!]+/)}
									<tr>
										<td class="font-mono text-xs">{parts[0]}</td>
										<td class="text-xs">{#if parts[1]}<Badge variant="outline" class="text-xs">{parts[1]}</Badge>{:else}<span class="text-muted-foreground">not pinned</span>{/if}</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				</Card.Content>
			</Card.Root>
		{/if}
	{/if}
</section>
