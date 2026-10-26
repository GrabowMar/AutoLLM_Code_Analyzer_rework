<script lang="ts">
	// Ad-hoc run: one application (or pasted files, labelled non-research)
	// under one analysis profile, with a live mini preflight from
	// `POST /runs/preview/`. Reads `?generation_job_id=` (application pages
	// link here). Research comparisons belong in studies.
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import type { GenerationJobList } from '$lib/api/generation';
	import type { AnalysisProfile, RunCreatePayload, RunPreview } from '$lib/api/analysis';
	import { createAnalysisRun, listAnalysisProfiles, previewAnalysisRun } from '$lib/api/analysis';
	import { apiFieldErrors, formatApiError, isAbortError } from '$lib/api/core';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Select from '$lib/components/ui/select';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Segmented } from '$lib/components/ui/segmented';
	import { toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import ApplicationPicker from '$lib/components/analysis/run/ApplicationPicker.svelte';
	import PastedFilesEditor, {
		toSourceCode,
		type PastedFile
	} from '$lib/components/analysis/run/PastedFilesEditor.svelte';
	import RunPreflight from '$lib/components/analysis/run/RunPreflight.svelte';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ClipboardPaste from '@lucide/svelte/icons/clipboard-paste';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Play from '@lucide/svelte/icons/play';
	import Sparkles from '@lucide/svelte/icons/sparkles';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';

	type Source = 'application' | 'paste';

	const presetJob = page.url.searchParams.get('generation_job_id') ?? '';

	// ── Target ─────────────────────────────────────────────────────────
	let source = $state<Source>('application');
	let jobId = $state(presetJob);
	let job = $state<GenerationJobList | null>(null);
	let pasted = $state<PastedFile[]>([{ id: 0, path: 'backend/app.py', content: '' }]);
	let name = $state('');

	const sourceCode = $derived(source === 'paste' ? toSourceCode(pasted) : null);
	const targetReady = $derived(
		source === 'application' ? !!jobId && (!job || job.status === 'completed') : sourceCode !== null
	);

	// ── Profiles ───────────────────────────────────────────────────────
	let profiles = $state<AnalysisProfile[]>([]);
	let profilesLoading = $state(true);
	let profilesError = $state('');
	let profileId = $state('');

	async function loadProfiles() {
		profilesLoading = true;
		profilesError = '';
		try {
			profiles = await listAnalysisProfiles();
			if (!profiles.some((p) => p.id === profileId)) {
				profileId =
					profiles.find((p) => p.slug === 'research-default')?.id ?? profiles[0]?.id ?? '';
			}
		} catch (e) {
			profilesError = formatApiError(e, 'Could not load analysis profiles.');
		} finally {
			profilesLoading = false;
		}
	}

	onMount(() => {
		void loadProfiles();
	});

	const profile = $derived(profiles.find((p) => p.id === profileId) ?? null);
	const systemProfiles = $derived(profiles.filter((p) => p.is_system));
	const myProfiles = $derived(profiles.filter((p) => !p.is_system));

	function reviewerSummary(p: AnalysisProfile): string {
		const r = p.summary.reviewer;
		if (!r.enabled) return 'no AI reviewer';
		return `AI ${r.engine === 'agent' ? `agent (${r.harness || 'harness'})` : 'single-shot'} · ${r.model} × ${r.samples ?? '—'}`;
	}

	// ── Preview (mini preflight) ───────────────────────────────────────
	let preview = $state<RunPreview | null>(null);
	let previewLoading = $state(false);
	let previewError = $state('');
	let fieldErrors = $state<Record<string, string>>({});
	let previewController: AbortController | null = null;
	let previewTimer: ReturnType<typeof setTimeout> | undefined;

	function payload(): RunCreatePayload | null {
		if (!profileId || !targetReady) return null;
		const base: RunCreatePayload = { profile_id: profileId };
		if (source === 'application') return { ...base, generation_job_id: jobId };
		return sourceCode ? { ...base, source_code: sourceCode } : null;
	}

	const payloadKey = $derived(
		JSON.stringify(
			profileId && targetReady
				? {
						profileId,
						source,
						jobId: source === 'application' ? jobId : null,
						code: source === 'paste' ? sourceCode : null
					}
				: null
		)
	);

	async function runPreview() {
		const body = payload();
		previewController?.abort();
		if (!body) {
			preview = null;
			previewLoading = false;
			previewError = '';
			return;
		}
		const mine = new AbortController();
		previewController = mine;
		previewLoading = true;
		try {
			preview = await previewAnalysisRun(body, { signal: mine.signal });
			previewError = '';
			fieldErrors = {};
		} catch (e) {
			if (isAbortError(e)) return;
			previewError = formatApiError(e, 'The preflight failed.');
			fieldErrors = apiFieldErrors(e);
		} finally {
			if (previewController === mine) previewLoading = false;
		}
	}

	$effect(() => {
		const key = payloadKey;
		untrack(() => {
			clearTimeout(previewTimer);
			if (key === 'null') {
				void runPreview();
				return;
			}
			previewLoading = true;
			previewTimer = setTimeout(() => void runPreview(), source === 'paste' ? 700 : 250);
		});
		return () => clearTimeout(previewTimer);
	});

	// The preview reflects the current inputs only once it has answered.
	const previewCurrent = $derived(!previewLoading && !!preview);
	// The preview leaves out study-design notes (single run).
	const blocking = $derived(preview?.blocking ?? []);
	const targetIneligible = $derived(!!preview?.targets?.[0] && !preview.targets[0].eligible);

	// ── Submit ─────────────────────────────────────────────────────────
	let submitting = $state(false);
	let submitError = $state('');
	let confirmOpen = $state(false);

	const canSubmit = $derived(!!payload() && !submitting && !profilesLoading && !previewLoading);

	function requestSubmit() {
		if (!canSubmit) return;
		if (blocking.length > 0 || targetIneligible) {
			confirmOpen = true;
			return;
		}
		void submit();
	}

	async function submit() {
		const body = payload();
		if (!body) return;
		submitting = true;
		submitError = '';
		try {
			const run = await createAnalysisRun({
				...body,
				name: name.trim() || undefined
			});
			confirmOpen = false;
			toast.success('Run queued');
			await goto(`/analysis/${run.id}`);
		} catch (e) {
			submitError = formatApiError(e, 'Could not start the run.');
			fieldErrors = apiFieldErrors(e);
			confirmOpen = false;
		} finally {
			submitting = false;
		}
	}

	const namePlaceholder = $derived(
		source === 'application'
			? `Ad-hoc analysis · ${jobId ? jobId.slice(0, 8) : '<job>'}`
			: 'Ad-hoc analysis · inline source'
	);
</script>

<div class="space-y-6">
	<PageHeader
		title="Ad-hoc run"
		description="Analyse one application, or files you paste, under an analysis profile. Ad-hoc runs are quick checks outside any study protocol; compare models in a study."
		back={{ href: '/analysis', label: 'Runs' }}
	>
		{#snippet actions()}
			<Button variant="outline" size="sm" href="/analysis/studies/new">
				<FlaskConical class="size-3.5" />New study instead
			</Button>
		{/snippet}
	</PageHeader>

	<div
		class="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,24rem)] xl:grid-cols-[minmax(0,1fr)_minmax(0,28rem)]"
	>
		<div class="min-w-0 space-y-6">
			<!-- Target -->
			<Card.Root>
				<Card.Header>
					<Card.Title>Target</Card.Title>
					<Card.Description>
						A completed generated application, or files pasted by hand.
					</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-4">
					<Segmented
						aria-label="Target source"
						bind:value={source}
						items={[
							{ value: 'application', label: 'Application', icon: AppWindow },
							{
								value: 'paste',
								label: 'Pasted files',
								icon: ClipboardPaste,
								badge: 'non-research'
							}
						]}
					/>
					{#if source === 'application'}
						<ApplicationPicker bind:selectedId={jobId} bind:selected={job} />
						{#if fieldErrors.generation_job_id}
							<p class="text-xs text-danger-fg" role="alert">
								{fieldErrors.generation_job_id}
							</p>
						{/if}
					{:else}
						<div
							class={cn(
								'flex items-start gap-2 rounded-md border px-3 py-2 text-xs',
								toneClasses.warning
							)}
							role="note"
						>
							<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
							<span>
								<span class="font-medium">Non-research.</span> Pasted source has no model, app, stack
								or generation metadata and no functional evaluation, so its results cannot join a study
								or a model comparison. Use it for quick checks only.
							</span>
						</div>
						<PastedFilesEditor bind:files={pasted} error={fieldErrors.source_code ?? ''} />
					{/if}
				</Card.Content>
			</Card.Root>

			<!-- Profile -->
			<Card.Root>
				<Card.Header>
					<Card.Title>Analysis profile</Card.Title>
					<Card.Description>
						Which tools run, the AI reviewer, the measured scope and outcome rules. The profile is
						frozen into the run's plan.
					</Card.Description>
				</Card.Header>
				<Card.Content class="space-y-3">
					{#if profilesError}
						<ErrorAlert message={profilesError} onretry={loadProfiles} />
					{:else}
						<Label for="profile-select" class="sr-only">Analysis profile</Label>
						<Select.Root
							type="single"
							value={profileId}
							onValueChange={(v) => (profileId = v)}
							items={profiles.map((p) => ({ value: p.id, label: p.name }))}
							disabled={profilesLoading}
						>
							<Select.Trigger
								id="profile-select"
								class="min-h-9 w-full py-1.5 data-[size=default]:h-auto"
							>
								{#if profilesLoading}
									<span class="flex items-center gap-2 text-muted-foreground">
										<LoaderCircle
											class="size-3.5 animate-spin motion-reduce:animate-none"
											aria-hidden="true"
										/>Loading profiles…
									</span>
								{:else if profile}
									<span class="flex min-w-0 flex-col items-start text-left">
										<span class="truncate font-medium"
											>{profile.name}
											<span class="font-normal text-muted-foreground">v{profile.version}</span
											></span
										>
										<span class="truncate text-[11px] text-muted-foreground">
											{profile.summary.tool_count} tools · {reviewerSummary(profile)}
										</span>
									</span>
								{:else}
									<span class="text-muted-foreground">Choose a profile</span>
								{/if}
							</Select.Trigger>
							<Select.Content>
								{#if systemProfiles.length}
									<Select.Group>
										<Select.GroupHeading>System</Select.GroupHeading>
										{#each systemProfiles as p (p.id)}
											<Select.Item value={p.id} label={p.name}>
												<span class="flex min-w-0 flex-col">
													<span class="truncate"
														>{p.name}
														<span class="text-muted-foreground">v{p.version}</span></span
													>
													<span class="truncate text-[11px] text-muted-foreground"
														>{p.summary.tool_count} tools · {reviewerSummary(p)}</span
													>
												</span>
											</Select.Item>
										{/each}
									</Select.Group>
								{/if}
								{#if myProfiles.length}
									<Select.Separator />
									<Select.Group>
										<Select.GroupHeading>Yours</Select.GroupHeading>
										{#each myProfiles as p (p.id)}
											<Select.Item value={p.id} label={p.name}>
												<span class="flex min-w-0 flex-col">
													<span class="truncate"
														>{p.name}
														<span class="text-muted-foreground">v{p.version}</span></span
													>
													<span class="truncate text-[11px] text-muted-foreground"
														>{p.summary.tool_count} tools · {reviewerSummary(p)}</span
													>
												</span>
											</Select.Item>
										{/each}
									</Select.Group>
								{/if}
							</Select.Content>
						</Select.Root>
						{#if fieldErrors.profile_id}
							<p class="text-xs text-danger-fg" role="alert">
								{fieldErrors.profile_id}
							</p>
						{/if}

						{#if profile}
							{#if profile.description}
								<p class="text-xs text-muted-foreground">
									{profile.description}
								</p>
							{/if}
							<div class="flex flex-wrap gap-1">
								{#each profile.summary.tools as slug (slug)}
									<span class="rounded border border-border px-1.5 font-mono text-[11px]"
										>{slug}</span
									>
								{/each}
								{#if profile.summary.reviewer.enabled}
									<Badge variant="outline" class="gap-1">
										<Sparkles class="size-3 text-accent-3" aria-hidden="true" />{reviewerSummary(
											profile
										)}
									</Badge>
								{/if}
							</div>
							<a
								href="/analysis/profiles/{profile.id}"
								class="inline-block text-xs text-primary underline-offset-2 hover:underline"
								>View or customize this profile</a
							>
						{/if}
					{/if}
				</Card.Content>
			</Card.Root>

			<!-- Name -->
			<Card.Root>
				<Card.Header>
					<Card.Title>Name</Card.Title>
					<Card.Description>Optional label in the runs list.</Card.Description>
				</Card.Header>
				<Card.Content>
					<Label for="run-name" class="sr-only">Run name</Label>
					<Input
						id="run-name"
						bind:value={name}
						maxlength={200}
						placeholder={namePlaceholder}
						aria-invalid={!!fieldErrors.name}
					/>
					{#if fieldErrors.name}
						<p class="mt-1 text-xs text-danger-fg" role="alert">
							{fieldErrors.name}
						</p>
					{/if}
				</Card.Content>
			</Card.Root>
		</div>

		<!-- Preflight + submit -->
		<div class="min-w-0 space-y-4 lg:sticky lg:top-4">
			<Card.Root>
				<Card.Header>
					<Card.Title>Preflight</Card.Title>
					<Card.Description>
						What will run on this target, checked against the analyzer image.
					</Card.Description>
				</Card.Header>
				<Card.Content>
					<RunPreflight
						{preview}
						loading={previewLoading}
						error={previewError}
						idle={source === 'application'
							? 'Choose an application to check it.'
							: 'Paste at least one file with a valid path.'}
					/>
				</Card.Content>
			</Card.Root>

			{#if submitError}
				<ErrorAlert message={submitError} />
			{/if}

			<div class="flex flex-wrap items-center justify-end gap-2">
				{#if !previewCurrent && payload() && previewLoading}
					<span class="text-xs text-muted-foreground">Checking…</span>
				{/if}
				<Button size="sm" onclick={requestSubmit} disabled={!canSubmit}>
					{#if submitting}
						<LoaderCircle class="size-3.5 animate-spin motion-reduce:animate-none" />
					{:else}
						<Play class="size-3.5" />
					{/if}
					Start run
				</Button>
			</div>
		</div>
	</div>
</div>

<Dialog.Root bind:open={confirmOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Start despite the preflight?</Dialog.Title>
			<Dialog.Description>
				The preflight found problems. The run may fail, or produce data that is incomplete or not
				comparable.
			</Dialog.Description>
		</Dialog.Header>
		<ul class="my-2 space-y-1.5 text-xs">
			{#if targetIneligible}
				<li class={cn('rounded-md border px-2.5 py-1.5', toneClasses.danger)}>
					The target is not eligible:
					{preview?.targets[0]?.reasons
						.filter((r) => r.severity === 'exclude')
						.map((r) => r.detail)
						.join('; ') || 'see the preflight'}
				</li>
			{/if}
			{#each blocking as issue, i (`${issue.code}-${i}`)}
				<li class={cn('rounded-md border px-2.5 py-1.5', toneClasses.danger)}>
					{issue.detail || issue.code}
				</li>
			{/each}
		</ul>
		<Dialog.Footer>
			<Button
				variant="outline"
				size="sm"
				disabled={submitting}
				onclick={() => (confirmOpen = false)}>Go back</Button
			>
			<Button size="sm" disabled={submitting} onclick={submit}>
				{#if submitting}<LoaderCircle
						class="size-3.5 animate-spin motion-reduce:animate-none"
					/>{/if}
				Start anyway
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
