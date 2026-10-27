<script lang="ts">
	/**
	 * SpecList — app specs as a table: identity (name, slug, description),
	 * category, difficulty, what the conformance suite can grade (endpoints,
	 * model fields) and row actions. Clicking a row opens it in the editor;
	 * catalog specs open read-only unless `canEditCatalog`.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import FileText from '@lucide/svelte/icons/file-text';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Eye from '@lucide/svelte/icons/eye';
	import Copy from '@lucide/svelte/icons/copy';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';
	import type { AppRequirementTemplate } from '$lib/api/generation';
	import { tableFrame } from '$lib/components/table';
	import { canEditSpec, specDataModels } from './spec';

	interface Props {
		specs: AppRequirementTemplate[];
		loading: boolean;
		activeSlug: string | null;
		canEditCatalog: boolean;
		/** Shown when `specs` is empty only because of a filter. */
		filtered?: boolean;
		onOpen: (s: AppRequirementTemplate) => void;
		onDuplicate: (s: AppRequirementTemplate) => void;
		onDelete: (s: AppRequirementTemplate) => void;
	}
	let { specs, loading, activeSlug, canEditCatalog, filtered = false, onOpen, onDuplicate, onDelete }: Props = $props();

	const LEVELS: Record<string, number> = { basic: 1, standard: 2, advanced: 3, hard: 3, extreme: 3 };
	const difficultyLevel = (d: string | null | undefined) => LEVELS[(d ?? '').toLowerCase()] ?? 0;

	/** Row click opens the spec, unless it landed on a control or the user is selecting text. */
	function openFromRow(e: MouseEvent, s: AppRequirementTemplate) {
		if ((e.target as HTMLElement | null)?.closest('a, button')) return;
		if (window.getSelection()?.toString()) return;
		onOpen(s);
	}

	/** Fields across every data model (v3 specs may list several). */
	function fieldCount(s: AppRequirementTemplate): number {
		return specDataModels(s).reduce((n, m) => n + Object.keys(m.fields).length, 0);
	}
</script>

{#if loading}
	<LoadingState label="loading app specs" />
{:else if specs.length === 0}
	<EmptyState
		icon={FileText}
		title={filtered ? 'No app specs match' : 'No app specs yet'}
		description={filtered ? 'Clear the search or category filter.' : 'Create one, or import a starter package.'}
	/>
{:else}
	<div class="table-frame" {@attach tableFrame}>
		<table>
			<thead>
				<tr>
					<th scope="col">App spec</th>
					<th scope="col" class="hidden md:table-cell">Category</th>
					<th scope="col" class="hidden sm:table-cell">Difficulty</th>
					<th scope="col" class="num hidden sm:table-cell" title="Endpoints the conformance suite calls">Endpoints</th>
					<th scope="col" class="num hidden sm:table-cell" title="Fields across all data models">Fields</th>
					<th scope="col"><span class="sr-only">Actions</span></th>
				</tr>
			</thead>
			<tbody>
				{#each specs as s (s.id)}
					{@const editable = canEditSpec(s, canEditCatalog)}
					{@const endpoints = (s.api_endpoints?.length ?? 0) + (s.admin_api_endpoints?.length ?? 0)}
					{@const level = difficultyLevel(s.difficulty)}
					<tr class="row-link" data-selected={activeSlug === s.slug} onclick={(e) => openFromRow(e, s)}>
						<td class="sm:min-w-[16rem]">
							<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
								<button type="button" class="cursor-pointer text-left text-sm font-medium hover:text-primary" onclick={() => onOpen(s)}>{s.name}</button>
								<span class="font-mono text-[11px] text-muted-foreground">{s.slug}</span>
								{#if !s.is_default}<Badge variant="outline" class="h-5 self-center px-1.5 text-[11px]">Yours</Badge>{/if}
							</div>
							{#if s.description}
								<p class="cell-sub mt-0.5 line-clamp-1 max-w-[60ch] text-xs text-muted-foreground" title={s.description}>{s.description}</p>
							{/if}
							<!-- Narrow screens: the hidden columns move under the name. -->
							<p class="mt-0.5 text-[11px] text-muted-foreground md:hidden">
								{[s.category, s.difficulty].filter(Boolean).join(' · ')}
								<span class="sm:hidden">
									{#if s.category || s.difficulty}·{/if}
									{#if endpoints}{endpoints} endpoints{:else}<span class="text-warning-fg">Not graded</span>{/if}
								</span>
							</p>
						</td>
						<td class="hidden whitespace-nowrap md:table-cell">
							{#if s.category}
								<span class="inline-flex h-5 items-center rounded-sm border border-border bg-muted px-1.5 text-[11px] text-muted-foreground">{s.category}</span>
							{:else}<span class="text-muted-foreground">—</span>{/if}
						</td>
						<td class="hidden whitespace-nowrap sm:table-cell">
							{#if s.difficulty}
								<span class="inline-flex items-center gap-2 text-xs capitalize text-muted-foreground">
									<span class="inline-flex items-end gap-0.5" aria-hidden="true">
										{#each [1, 2, 3] as step (step)}
											<span class="w-1 rounded-[1px] {step <= level ? 'bg-foreground/70' : 'bg-border'}" style="height: {4 + step * 3}px"></span>
										{/each}
									</span>
									{s.difficulty}
								</span>
							{:else}<span class="text-muted-foreground">—</span>{/if}
						</td>
						<td class="num hidden sm:table-cell">
							{#if endpoints}{endpoints}{:else}<span class="font-sans text-[11px] text-warning-fg" title="Agent runs on this spec cannot be graded">Not graded</span>{/if}
						</td>
						<td class="num hidden sm:table-cell">{fieldCount(s) || '—'}</td>
						<td class="w-px whitespace-nowrap text-right">
							<div class="flex items-center justify-end gap-0.5">
								<div class="row-actions-more flex items-center gap-0.5">
									<Button
										variant="ghost"
										size="sm"
										class="size-8 p-0 text-muted-foreground"
										href="/sample-generator?app={encodeURIComponent(s.slug)}"
										title="Generate samples from this spec"
										aria-label="Generate samples from {s.name}"
									>
										<WandSparkles />
									</Button>
									<Button variant="ghost" size="sm" class="size-8 p-0 text-muted-foreground" title="Duplicate" aria-label="Duplicate {s.name}" onclick={() => onDuplicate(s)}>
										<Copy />
									</Button>
									{#if !s.is_default}
										<Button
											variant="ghost"
											size="sm"
											class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
											title="Delete app spec"
											aria-label="Delete app spec {s.name}"
											onclick={() => onDelete(s)}
										>
											<Trash2 />
										</Button>
									{/if}
								</div>
								<Button
									variant="ghost"
									size="sm"
									class="size-8 p-0 text-muted-foreground"
									title={editable ? 'Edit' : 'View'}
									aria-label="{editable ? 'Edit' : 'View'} {s.name}"
									onclick={() => onOpen(s)}
								>
									{#if editable}<Pencil />{:else}<Eye />{/if}
								</Button>
							</div>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
