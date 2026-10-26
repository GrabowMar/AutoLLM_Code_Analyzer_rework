<script lang="ts">
	import { onMount } from 'svelte';
	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/state';
	import { toast } from 'svelte-sonner';
	import { apiFieldErrors, formatApiError } from '$lib/api/core';
	import {
		createAnalysisProfile,
		getAnalysisProfile,
		type AnalysisProfileDetail,
		type ProfileConfig,
		type ProfileDefaults
	} from '$lib/api/analysis';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import ProfileEditor, {
		type ProfileEditorSection,
		type ProfileValidationState
	} from '$lib/components/analysis/profile/ProfileEditor.svelte';
	import { loadProfileDefaults } from '$lib/components/analysis/profile/defaults';
	import { cloneJson, stableStringify } from '$lib/components/analysis/profile/profile-utils';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import Plus from '@lucide/svelte/icons/plus';

	// Mirrors ProfileCreateSchema.slug.
	const SLUG_RE = /^[-a-zA-Z0-9_]+$/;

	let defaults = $state<ProfileDefaults | null>(null);
	let source = $state<AnalysisProfileDetail | null>(null);
	let loading = $state(true);
	let loadError = $state('');

	let draft = $state<ProfileConfig | null>(null);
	let initialKey = '';
	let name = $state('');
	let slug = $state('');
	let description = $state('');
	let section = $state<ProfileEditorSection>('tools');
	let validation = $state<ProfileValidationState>({
		status: 'idle',
		errors: {},
		contentHash: null,
		config: null,
		message: ''
	});
	let invalid = $state(false);
	let creating = $state(false);
	let createError = $state('');
	let fieldErrors = $state<Record<string, string>>({});
	let created = false;

	const fromId = page.url.searchParams.get('from');

	async function load() {
		loading = true;
		loadError = '';
		try {
			const [catalog, base] = await Promise.all([
				loadProfileDefaults(),
				fromId ? getAnalysisProfile(fromId) : null
			]);
			defaults = catalog;
			source = base;
			draft = cloneJson(base ? base.config : catalog.config);
			initialKey = stableStringify(draft);
			if (base) {
				name = `${base.name} (variant)`.slice(0, 200);
				description = base.description;
			}
		} catch (e) {
			loadError = formatApiError(
				e,
				fromId ? 'Could not load the source profile.' : 'Could not load the default configuration.'
			);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		void load();
	});

	const slugTrimmed = $derived(slug.trim());
	const slugInvalid = $derived(
		!!slugTrimmed && (!SLUG_RE.test(slugTrimmed) || slugTrimmed.length > 100)
	);
	const dirty = $derived(
		!!draft &&
			(stableStringify(draft) !== initialKey ||
				!!name.trim() ||
				!!slugTrimmed ||
				(!source && !!description))
	);
	const canCreate = $derived(
		!!draft &&
			!!name.trim() &&
			!slugInvalid &&
			!creating &&
			!invalid &&
			validation.status !== 'pending' &&
			validation.status !== 'invalid' &&
			validation.status !== 'idle'
	);
	const editorErrors = $derived(
		Object.fromEntries(
			Object.entries(fieldErrors).filter(([key]) => key === 'config' || key.startsWith('config.'))
		)
	);

	async function create(event?: SubmitEvent) {
		event?.preventDefault();
		if (!draft || !canCreate) return;
		creating = true;
		createError = '';
		fieldErrors = {};
		try {
			const profile = await createAnalysisProfile({
				name: name.trim(),
				description,
				...(slugTrimmed ? { slug: slugTrimmed } : {}),
				config: draft
			});
			created = true;
			toast.success(`Created ${profile.name} (v${profile.version})`);
			await goto(`/analysis/profiles/${profile.id}`);
		} catch (e) {
			const fields = apiFieldErrors(e);
			const body = e as { field?: string; detail?: unknown };
			if (body?.field && typeof body.detail === 'string') fields[body.field] = body.detail;
			fieldErrors = fields;
			createError =
				Object.keys(fields).length === 0
					? formatApiError(e, 'Could not create the profile.')
					: 'Fix the highlighted fields and try again.';
		} finally {
			creating = false;
		}
	}

	beforeNavigate((navigation) => {
		if (created || !dirty || creating) return;
		if (navigation.type === 'leave') {
			navigation.cancel();
			return;
		}
		if (!confirm('Discard this new profile?')) navigation.cancel();
	});
</script>

<div class="space-y-6">
	<PageHeader
		title="New analysis profile"
		description={source
			? `Starting from ${source.name} (${source.slug}@v${source.version}). The new profile is yours and starts at v1.`
			: 'Starting from the research default: every default static tool plus the AI reviewer. The new profile is yours and starts at v1.'}
		back={{ href: '/analysis/profiles', label: 'Profiles' }}
	>
		{#snippet actions()}
			<Button href="/analysis/profiles" variant="outline" size="sm">Cancel</Button>
			<Button size="sm" disabled={!canCreate} onclick={() => create()}>
				{#if creating}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{:else}<Plus
					/>{/if}
				Create profile
			</Button>
		{/snippet}
	</PageHeader>

	{#if loading}
		<LoadingState label="loading defaults" />
	{:else if loadError}
		<ErrorAlert message={loadError} onretry={load} />
	{:else if draft}
		{#if createError}<ErrorAlert message={createError} />{/if}

		<Card.Root>
			<Card.Header class="pb-3">
				<Card.Title class="text-base">Details</Card.Title>
				<Card.Description
					>The slug identifies every version of the profile in runs, studies and exports.</Card.Description
				>
			</Card.Header>
			<Card.Content>
				<form
					class="grid gap-4 md:grid-cols-[minmax(0,16rem)_minmax(0,12rem)_minmax(0,1fr)]"
					onsubmit={create}
				>
					<div class="space-y-1.5">
						<Label for="new-profile-name">Name</Label>
						<Input
							id="new-profile-name"
							bind:value={name}
							maxlength={200}
							required
							autocomplete="off"
							placeholder="Thesis security core"
							aria-invalid={fieldErrors.name ? true : undefined}
						/>
						{#if fieldErrors.name}<p class="text-[11px] text-danger-fg">{fieldErrors.name}</p>{/if}
					</div>
					<div class="space-y-1.5">
						<Label for="new-profile-slug"
							>Slug <span class="font-normal text-muted-foreground">(optional)</span></Label
						>
						<Input
							id="new-profile-slug"
							bind:value={slug}
							oninput={() => {
								if (fieldErrors.slug) fieldErrors = { ...fieldErrors, slug: '' };
							}}
							autocomplete="off"
							spellcheck={false}
							placeholder="from the name"
							class="font-mono text-xs"
							aria-invalid={slugInvalid || fieldErrors.slug ? true : undefined}
							aria-describedby="new-profile-slug-help"
						/>
						<p
							id="new-profile-slug-help"
							class="text-[11px] {slugInvalid || fieldErrors.slug
								? 'text-danger-fg'
								: 'text-muted-foreground'}"
						>
							{fieldErrors.slug ||
								(slugInvalid ? 'Letters, digits, hyphens and underscores.' : 'Must be unique.')}
						</p>
					</div>
					<div class="space-y-1.5">
						<Label for="new-profile-description">Description</Label>
						<Textarea
							id="new-profile-description"
							bind:value={description}
							rows={2}
							maxlength={5000}
							class="min-h-9"
							placeholder="What this profile measures and why"
						/>
						{#if fieldErrors.description}<p class="text-[11px] text-danger-fg">
								{fieldErrors.description}
							</p>{/if}
					</div>
					<!-- Enter in a field submits the form. -->
					<button type="submit" class="hidden" tabindex="-1" aria-hidden="true">Create</button>
				</form>
			</Card.Content>
		</Card.Root>

		<Card.Root>
			<Card.Content class="pt-4">
				<ProfileEditor
					config={draft}
					{defaults}
					errors={editorErrors}
					bind:validation
					bind:invalid
					bind:section
					onchange={(next) => (draft = next)}
				/>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
