<script lang="ts">
	/**
	 * SelectionScope — the sample of a study report built from the newest unique runs across experiments
	 * (the final assessment): a checklist of experiments (search, tick all shown, clear), optional model
	 * and task filters (multi-select menus whose options come from the ticked experiments' designs, else
	 * from the model and task catalogs) and the complete-cases switch.
	 *
	 * Selections are `$bindable` and replaced, never mutated. Ticking nothing pools every experiment the
	 * user can see; an empty filter keeps every model / task.
	 *
	 * Props: `studies` (launched experiments), `loaded`, `catalogModels`, `catalogTasks` (fallback
	 * options `{ value, label }`), bindable `studyIds`, `models`, `tasks`, `commonTasksOnly`.
	 */
	import Search from '@lucide/svelte/icons/search';
	import X from '@lucide/svelte/icons/x';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import FilterMenu, { type FilterMenuOption } from '$lib/components/FilterMenu.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { getStudy } from '$lib/api/v3/studies';
	import type { StudyListOut } from '$lib/api/v3/types';
	import { matchesAll, searchTokens } from '$lib/utils/search';
	import { formatDate } from '$lib/utils/formatters';
	import { cn } from '$lib/utils';
	import { designScope, modelShortLabels, shortId, unionWithCounts } from './selection';

	interface Props {
		studies: StudyListOut[];
		loaded: boolean;
		catalogModels: { value: string; label: string }[];
		catalogTasks: { value: string; label: string }[];
		studyIds?: string[];
		models?: string[];
		tasks?: string[];
		commonTasksOnly?: boolean;
	}

	let {
		studies,
		loaded,
		catalogModels,
		catalogTasks,
		studyIds = $bindable([]),
		models = $bindable([]),
		tasks = $bindable([]),
		commonTasksOnly = $bindable(false)
	}: Props = $props();

	let query = $state('');
	const tokens = $derived(searchTokens(query));
	const shown = $derived(studies.filter((s) => matchesAll([s.name, s.status, s.mode, s.kind], tokens)));
	/**
	 * Ticked ids the list does not hold (a deep link to an experiment you cannot see, or a draft). Until the
	 * list has loaded they are only pending: shown neutrally, never as unknown.
	 */
	const unlisted = $derived(studyIds.filter((id) => !studies.some((s) => s.id === id)));
	const unknown = $derived(loaded ? unlisted : []);
	const pending = $derived(loaded ? [] : unlisted);

	function toggle(id: string, on: boolean) {
		studyIds = on ? (studyIds.includes(id) ? studyIds : [...studyIds, id]) : studyIds.filter((x) => x !== id);
	}
	function tickShown() {
		studyIds = [...studyIds, ...shown.map((s) => s.id).filter((id) => !studyIds.includes(id))];
	}

	// The ticked experiments' designs (models and task units), loaded once each for the filter options.
	let designs = $state<Record<string, { models: string[]; tasks: string[] } | null>>({});
	const requested = new Set<string>();
	$effect(() => {
		for (const id of studyIds) {
			if (requested.has(id)) continue;
			requested.add(id);
			getStudy(id)
				.then((s) => (designs = { ...designs, [id]: designScope(s.design) }))
				.catch(() => (designs = { ...designs, [id]: null }));
		}
	});
	const tickedDesigns = $derived(studyIds.map((id) => designs[id]).filter((d): d is { models: string[]; tasks: string[] } => !!d));
	const designsLoading = $derived(studyIds.some((id) => !(id in designs)));
	const fromDesigns = $derived(studyIds.length > 0 && tickedDesigns.length > 0);

	/** Options, keeping picked values the option list lacks (so a deep link never loses a filter). */
	function withPicked(options: FilterMenuOption[], picked: string[], label: (v: string) => string): FilterMenuOption[] {
		const extra = picked.filter((v) => !options.some((o) => o.value === v)).map((v) => ({ value: v, label: label(v), hint: 'picked' }));
		return [...options, ...extra];
	}
	const modelOptions = $derived(
		withPicked(
			fromDesigns
				? designModelOptions(unionWithCounts(tickedDesigns.map((d) => d.models)), models)
				: catalogModels.map((m) => ({ value: m.value, label: m.label })),
			models,
			(v) => modelShortLabels([...models, ...tickedDesigns.flatMap((d) => d.models)]).get(v) ?? v
		)
	);
	/** Design models as options: the short name, or the full id when two endpoints share it. */
	function designModelOptions(found: [string, number][], picked: string[]): FilterMenuOption[] {
		const labels = modelShortLabels([...found.map(([v]) => v), ...picked]);
		return found.map(([value, n]) => ({ value, label: labels.get(value) ?? value, hint: String(n) }));
	}
	const taskOptions = $derived(
		withPicked(
			fromDesigns
				? unionWithCounts(tickedDesigns.map((d) => d.tasks)).map(([value, n]) => ({ value, label: value, hint: String(n) }))
				: catalogTasks.map((t) => ({ value: t.value, label: t.label, hint: 'any v' })),
			tasks,
			(v) => v
		)
	);
	const list = (v: string) => (v ? v.split(',') : []);
	const modelLabel = (v: string) => modelOptions.find((o) => o.value === v)?.label ?? v;
</script>

{#snippet chips(values: string[], label: (v: string) => string, remove: (v: string) => void, noun: string)}
	{#if values.length}
		<ul class="flex flex-wrap gap-1" aria-label="Picked {noun}">
			{#each values as v (v)}
				<li class="inline-flex h-6 items-center gap-1 rounded-sm border border-border bg-muted/40 pl-1.5 pr-0.5 text-xs" title={v}>
					<span class="max-w-52 truncate font-mono text-[11px]">{label(v)}</span>
					<button
						type="button"
						class="inline-flex size-5 cursor-pointer items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
						aria-label="Remove {label(v)}"
						onclick={() => remove(v)}><X class="size-3" aria-hidden="true" /></button
					>
				</li>
			{/each}
		</ul>
	{/if}
{/snippet}

<div class="space-y-4">
	<!-- Experiments -->
	<div class="space-y-1.5">
		<p id="sel-studies-label" class="text-sm font-medium">Experiments to pool</p>
		<p class="text-xs text-muted-foreground">
			Per model and task the newest usable run among these experiments counts; reruns and retries are superseded and listed. Pilot-stage,
			beyond-plan and reference runs never count.
		</p>
		<div class="overflow-clip rounded-md border border-border" role="group" aria-labelledby="sel-studies-label">
			<div class="flex flex-wrap items-center gap-2 border-b border-border/60 bg-muted/20 px-3 py-2">
				<label class="relative flex min-w-36 flex-1 items-center">
					<Search class="pointer-events-none absolute left-2 size-3.5 text-muted-foreground" aria-hidden="true" />
					<input
						type="search"
						bind:value={query}
						placeholder="Filter experiments…"
						aria-label="Filter experiments"
						class="h-7 w-full rounded-md border border-border bg-background pl-7 pr-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
						onkeydown={(e) => {
							if (e.key === 'Escape' && query) {
								e.preventDefault();
								query = '';
							}
						}}
					/>
				</label>
				<!-- The two buttons wrap together, never one alone. -->
				<div class="flex shrink-0 gap-2">
					<button
						type="button"
						class="inline-flex h-7 cursor-pointer items-center rounded-md border border-border px-2.5 text-xs font-medium transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-ring"
						disabled={shown.length === 0}
						onclick={tickShown}>Tick {tokens.length ? 'shown' : 'all'} ({shown.length})</button
					>
					<button
						type="button"
						class="inline-flex h-7 cursor-pointer items-center rounded-md border border-border px-2.5 text-xs font-medium transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-ring"
						disabled={studyIds.length === 0}
						onclick={() => (studyIds = [])}>None</button
					>
				</div>
			</div>
			<ul class="max-h-80 divide-y divide-border/60 overflow-y-auto">
				{#each pending as id (id)}
					<li>
						<label class="flex cursor-pointer items-center gap-2.5 px-3 py-2 hover:bg-muted/40">
							<Checkbox checked onCheckedChange={(v) => toggle(id, v === true)} />
							<span class="min-w-0 flex-1">
								<span class="block truncate font-mono text-xs" title={id}>{id}</span>
								<span class="block text-[11px] text-muted-foreground">Ticked by the link; loading its name…</span>
							</span>
						</label>
					</li>
				{/each}
				{#each unknown as id (id)}
					<li>
						<label class="flex cursor-pointer items-center gap-2.5 px-3 py-2 hover:bg-muted/40">
							<Checkbox checked onCheckedChange={(v) => toggle(id, v === true)} />
							<span class="min-w-0 flex-1">
								<span class="block truncate font-mono text-xs" title={id}>{id}</span>
								<span class="block text-[11px] text-muted-foreground"
									>Not among the experiments listed here; the server checks access when the report is generated.</span
								>
							</span>
						</label>
					</li>
				{/each}
				{#each shown as s (s.id)}
					{@const on = studyIds.includes(s.id)}
					<li>
						<label class={cn('flex cursor-pointer items-center gap-2.5 px-3 py-2 transition-colors hover:bg-muted/40', on && 'bg-primary/[0.05]')}>
							<Checkbox checked={on} onCheckedChange={(v) => toggle(s.id, v === true)} />
							<span class="min-w-0 flex-1">
								<span class="block break-words text-sm max-sm:line-clamp-2 sm:truncate" title={s.name}>{s.name}</span>
								<span class="block truncate text-[11px] text-muted-foreground">
									<span class="font-mono" title={s.id}>{shortId(s.id)}</span> · {s.kind} · {s.mode} mode{s.created_at ? ` · ${formatDate(s.created_at)}` : ''}
								</span>
							</span>
							<StatusBadge status={s.status} />
							<span class="hidden w-24 shrink-0 text-right font-mono text-[11px] tabular-nums text-muted-foreground sm:block" title="Cells done / planned">
								{s.cells_done}/{s.cells_total} cells
							</span>
						</label>
					</li>
				{:else}
					<li class="px-3 py-4 text-center text-xs text-muted-foreground">
						{#if !loaded}Loading experiments…{:else if studies.length === 0}No launched experiments yet.{:else}No experiment matches “{query}”.{/if}
					</li>
				{/each}
			</ul>
			<p class="border-t border-border/60 px-3 py-1.5 text-xs text-muted-foreground" role="status">
				{#if studyIds.length}
					<b class="font-mono font-medium tabular-nums text-foreground">{studyIds.length}</b>{loaded ? ` of ${studies.length}` : ''} ticked
				{:else}
					None ticked: every experiment you can see is pooled.
				{/if}
			</p>
		</div>
	</div>

	<!-- Filters -->
	<div class="grid gap-4 @xl:grid-cols-2">
		<div class="min-w-0 space-y-1.5">
			<p class="text-sm font-medium" id="sel-models-label">Models</p>
			<div class="flex flex-wrap items-center gap-2">
				<FilterMenu
					label="Models"
					multiple
					searchable
					anyLabel="All models"
					caption={fromDesigns ? 'In the ticked experiments (hint: how many ran it)' : 'From the model catalog; tick experiments to list their models'}
					value={models.join(',')}
					options={modelOptions}
					onChange={(v) => (models = list(v))}
				/>
				{#if designsLoading}<LoaderCircle class="size-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-label="Loading experiment designs" />{/if}
				{#if !models.length}<span class="text-xs text-muted-foreground">All models</span>{/if}
			</div>
			{@render chips(models, modelLabel, (v) => (models = models.filter((x) => x !== v)), 'models')}
		</div>
		<div class="min-w-0 space-y-1.5">
			<p class="text-sm font-medium">Tasks</p>
			<div class="flex flex-wrap items-center gap-2">
				<FilterMenu
					label="Tasks"
					multiple
					searchable
					anyLabel="All tasks"
					caption={fromDesigns ? 'In the ticked experiments (hint: how many ran it)' : 'Library tasks, any version; tick experiments to list their tasks'}
					value={tasks.join(',')}
					options={taskOptions}
					onChange={(v) => (tasks = list(v))}
				/>
				{#if !tasks.length}<span class="text-xs text-muted-foreground">All tasks</span>{/if}
			</div>
			{@render chips(tasks, (v) => v, (v) => (tasks = tasks.filter((x) => x !== v)), 'tasks')}
		</div>
	</div>

	<label class="flex cursor-pointer items-start gap-2.5 rounded-md border border-border p-3 transition-colors hover:bg-muted/40">
		<Checkbox bind:checked={commonTasksOnly} class="mt-0.5" />
		<span class="space-y-0.5">
			<span class="block text-sm font-medium">Only tasks every model has <span class="font-normal text-muted-foreground">(complete cases)</span></span>
			<span class="block text-xs leading-relaxed text-muted-foreground">
				Keeps a task only when every group has a selected usable run for it, so every model is compared on the same tasks; dropped tasks are
				listed in the report. Off: each model keeps all its tasks and the report warns when task sets differ.
			</span>
		</span>
	</label>
</div>
