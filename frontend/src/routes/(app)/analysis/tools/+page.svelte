<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { formatApiError } from '$lib/api/core';
	import {
		buildAnalyzerImage,
		getAnalyzerImage,
		getLatestSelftest,
		isRunActive,
		listAnalysisRuns,
		listAnalysisTools,
		startSelftest,
		type AnalysisRunListItem,
		type AnalysisTool,
		type ImageStatus,
		type SelftestLatest,
		type ToolKind
	} from '$lib/api/analysis';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Segmented } from '$lib/components/ui/segmented';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { categoryLabel } from '$lib/components/analysis/profile/profile-utils';
	import ImageCard from './ImageCard.svelte';
	import SelftestCard from './SelftestCard.svelte';
	import ToolCard from './ToolCard.svelte';
	import Search from '@lucide/svelte/icons/search';
	import SlidersHorizontal from '@lucide/svelte/icons/sliders-horizontal';

	const IMAGE_POLL_MS = 5000;
	const SELFTEST_POLL_MS = 3000;

	let tools = $state<AnalysisTool[]>([]);
	let toolsLoading = $state(true);
	let toolsError = $state('');

	let image = $state<ImageStatus | null>(null);
	let imageError = $state('');
	let buildRequesting = $state(false);

	let selftest = $state<SelftestLatest | null>(null);
	let selftestError = $state('');
	let activeRun = $state<AnalysisRunListItem | null>(null);
	let starting = $state(false);

	async function loadTools(silent = false) {
		if (!silent) toolsLoading = true;
		try {
			tools = await listAnalysisTools();
			toolsError = '';
		} catch (e) {
			if (!silent || tools.length === 0)
				toolsError = formatApiError(e, 'Could not load the tool catalog.');
		} finally {
			toolsLoading = false;
		}
	}

	async function loadImage(refresh = false) {
		try {
			image = await getAnalyzerImage({ refresh });
			imageError = '';
		} catch (e) {
			imageError = formatApiError(e, 'Could not read the analyzer image status.');
		}
	}

	async function loadSelftest() {
		try {
			selftest = await getLatestSelftest();
			selftestError = '';
		} catch (e) {
			selftestError = formatApiError(e, 'Could not load the self-test results.');
		}
	}

	/** The newest self-test still in flight (resumes polling after a reload). */
	async function findActiveRun() {
		try {
			const page = await listAnalysisRuns({
				purpose: 'canary',
				status: ['queued', 'running'],
				per_page: 1
			});
			activeRun = page.items[0] ?? null;
		} catch {
			// Advisory: without it the page simply does not show progress.
		}
	}

	function loadAll() {
		void loadTools();
		void loadImage();
		void loadSelftest();
		void findActiveRun();
	}

	onMount(loadAll);

	// ── image build ────────────────────────────────────────────────────
	async function build() {
		buildRequesting = true;
		try {
			image = await buildAnalyzerImage();
			if (image.built) toast.success('The analyzer image is already built.');
			else toast.info('Building the analyzer image in the background.');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not start the image build.'));
		} finally {
			buildRequesting = false;
		}
	}

	// Poll while a build runs; once it finishes, versions become verified.
	const building = $derived(!!image?.building);
	let wasBuilding = false;

	$effect(() => {
		const now = building;
		if (wasBuilding && !now) {
			untrack(() => {
				if (image?.built) toast.success('Analyzer image built.');
				else if (image?.error) toast.error(`Image build failed: ${image.error}`);
				void loadTools(true);
				void loadSelftest();
			});
		}
		wasBuilding = now;
	});

	$effect(() => {
		if (!building) return;
		const timer = setInterval(() => void loadImage(true), IMAGE_POLL_MS);
		return () => clearInterval(timer);
	});

	// ── self-test ──────────────────────────────────────────────────────
	async function runSelftest() {
		starting = true;
		try {
			activeRun = await startSelftest();
			toast.info('Self-test queued.');
		} catch (e) {
			toast.error(formatApiError(e, 'Could not start the self-test.'));
		} finally {
			starting = false;
		}
	}

	const activeId = $derived(activeRun?.id ?? null);

	$effect(() => {
		const id = activeId;
		if (!id) return;
		const controller = new AbortController();
		const timer = setInterval(async () => {
			try {
				const page = await listAnalysisRuns(
					{ purpose: 'canary', per_page: 10 },
					{ signal: controller.signal }
				);
				const run = page.items.find((r) => r.id === id);
				if (controller.signal.aborted) return;
				if (!run || !isRunActive(run.status)) {
					activeRun = null;
					if (run) {
						if (run.status === 'completed') toast.success('Self-test finished.');
						else
							toast.warning(
								`Self-test ${run.status}${run.error_message ? `: ${run.error_message}` : ''}`
							);
					}
					void loadSelftest();
					void loadTools(true);
				} else {
					activeRun = run;
				}
			} catch {
				// Aborted, or a transient failure: keep the last progress; the next tick retries.
			}
		}, SELFTEST_POLL_MS);
		return () => {
			controller.abort();
			clearInterval(timer);
		};
	});

	const selftestBlocked = $derived.by(() => {
		if (image && !image.daemon_ok) return 'The analysis Docker daemon is unreachable.';
		if (image?.building) return 'Wait for the image build to finish.';
		return '';
	});

	// ── catalog filters ────────────────────────────────────────────────
	type KindFilter = 'all' | ToolKind;
	let kind = $state<KindFilter>('all');
	let search = $state('');

	const query = $derived(search.trim().toLowerCase());
	const filtered = $derived(
		tools.filter(
			(t) =>
				(kind === 'all' || t.kind === kind) &&
				(!query ||
					t.name.toLowerCase().includes(query) ||
					t.slug.includes(query) ||
					t.category.includes(query) ||
					categoryLabel(t.category).toLowerCase().includes(query) ||
					t.languages.some((l) => l.includes(query)))
		)
	);
	const kindCounts = $derived({
		all: tools.length,
		static: tools.filter((t) => t.kind === 'static').length,
		metrics: tools.filter((t) => t.kind === 'metrics').length,
		ai: tools.filter((t) => t.kind === 'ai').length
	});
	const unverifiedCount = $derived(
		tools.filter((t) => t.kind !== 'ai' && t.version_source !== 'image_manifest').length
	);
</script>

<div class="space-y-6">
	<PageHeader
		title="Analysis tools"
		description="The static analyzers and metrics tools baked into the pinned analyzer image, their verified versions and self-test evidence. Profiles choose which tools run and how."
	>
		{#snippet actions()}
			<Button href="/analysis/profiles" variant="outline" size="sm"
				><SlidersHorizontal />Profiles</Button
			>
		{/snippet}
	</PageHeader>

	<div class="grid gap-4 xl:grid-cols-2">
		<ImageCard
			{image}
			error={imageError}
			requesting={buildRequesting}
			onbuild={build}
			onrefresh={() => loadImage(true)}
		/>
		<SelftestCard
			{selftest}
			error={selftestError}
			{tools}
			active={activeRun}
			{starting}
			canStart={!selftestBlocked}
			startBlockedReason={selftestBlocked}
			onstart={runSelftest}
			onrefresh={loadSelftest}
		/>
	</div>

	<section class="space-y-4" aria-labelledby="catalog-heading">
		<div class="flex flex-wrap items-end justify-between gap-3">
			<div>
				<h2 id="catalog-heading" class="text-lg font-semibold">Catalog</h2>
				<p class="text-sm text-muted-foreground">
					{#if unverifiedCount > 0}
						{unverifiedCount} version{unverifiedCount === 1 ? ' is' : 's are'} unverified until the image
						is built.
					{:else if tools.length > 0}
						Every static version is read from the built image's manifest.
					{/if}
				</p>
			</div>
			<div class="flex w-full flex-wrap items-center gap-2 sm:w-auto">
				<Segmented
					aria-label="Tool kind"
					size="sm"
					bind:value={kind}
					items={[
						{ value: 'all', label: 'All', badge: String(kindCounts.all) },
						{ value: 'static', label: 'Static', badge: String(kindCounts.static) },
						{ value: 'metrics', label: 'Metrics', badge: String(kindCounts.metrics) },
						{ value: 'ai', label: 'AI', badge: String(kindCounts.ai) }
					]}
				/>
				<div class="relative w-full sm:w-56">
					<Search
						class="pointer-events-none absolute left-2.5 top-2 size-4 text-muted-foreground"
						aria-hidden="true"
					/>
					<Input
						bind:value={search}
						type="search"
						placeholder="Name, category, language…"
						aria-label="Search tools"
						class="h-8 pl-8 text-xs"
					/>
				</div>
			</div>
		</div>

		{#if toolsError}<ErrorAlert message={toolsError} onretry={() => loadTools()} />{/if}
		{#if toolsLoading && tools.length === 0}
			<LoadingState label="loading tools" />
		{:else if !toolsError && filtered.length === 0}
			<EmptyState
				compact
				icon={Search}
				title={tools.length === 0 ? 'The catalog is empty' : 'No tools match'}
				description={tools.length === 0
					? 'Seed the analysis tools on the backend.'
					: 'Clear the search or pick another kind.'}
			/>
		{:else}
			<div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
				{#each filtered as tool, i (tool.slug)}
					<ToolCard {tool} index={i} />
				{/each}
			</div>
		{/if}
	</section>
</div>
