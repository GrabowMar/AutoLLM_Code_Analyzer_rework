<script lang="ts">
	/**
	 * ProfileCloneDialog — copy a profile version into a new profile of yours
	 * (version 1). System profiles are customised this way.
	 *
	 * Props: `open` (bindable), `profile` (the version to copy; null closes),
	 * `oncloned(profile)` with the new profile.
	 */
	import { apiFieldErrors, formatApiError } from '$lib/api/core';
	import { cloneAnalysisProfile, type AnalysisProfile } from '$lib/api/analysis';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		open: boolean;
		profile: AnalysisProfile | null;
		oncloned?: (profile: AnalysisProfile) => void;
	}

	let { open = $bindable(false), profile, oncloned }: Props = $props();

	// Mirrors ProfileCloneSchema.slug.
	const SLUG_RE = /^[-a-zA-Z0-9_]+$/;

	let name = $state('');
	let slug = $state('');
	let busy = $state(false);
	let error = $state('');
	let fieldErrors = $state<Record<string, string>>({});

	// Reset the form each time the dialog opens for a profile.
	let openedFor = '';
	$effect(() => {
		if (open && profile && openedFor !== profile.id) {
			openedFor = profile.id;
			name = `${profile.name} (copy)`.slice(0, 200);
			slug = '';
			error = '';
			fieldErrors = {};
		}
		if (!open) openedFor = '';
	});

	const nameTrimmed = $derived(name.trim());
	const slugTrimmed = $derived(slug.trim());
	const slugInvalid = $derived(
		!!slugTrimmed && (!SLUG_RE.test(slugTrimmed) || slugTrimmed.length > 100)
	);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (!profile || !nameTrimmed || slugInvalid || busy) return;
		busy = true;
		error = '';
		fieldErrors = {};
		try {
			const created = await cloneAnalysisProfile(profile.id, {
				name: nameTrimmed,
				...(slugTrimmed ? { slug: slugTrimmed } : {})
			});
			open = false;
			oncloned?.(created);
		} catch (e) {
			const body = e as { field?: string; detail?: unknown };
			const fields = apiFieldErrors(e);
			if (body?.field && typeof body.detail === 'string') fields[body.field] = body.detail;
			fieldErrors = fields;
			if (Object.keys(fields).length === 0)
				error = formatApiError(e, 'Could not clone the profile.');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-md">
		<form class="space-y-4" onsubmit={submit}>
			<Dialog.Header>
				<Dialog.Title
					>{profile?.is_system ? 'Customize' : 'Clone'} {profile?.name ?? 'profile'}</Dialog.Title
				>
				<Dialog.Description>
					Creates a new profile of yours (v1) with the configuration of
					<span class="font-mono">{profile ? `${profile.slug}@v${profile.version}` : ''}</span>.
					Runs that used the original keep it.
				</Dialog.Description>
			</Dialog.Header>
			<div class="space-y-1.5">
				<Label for="clone-profile-name">Name</Label>
				<Input
					id="clone-profile-name"
					bind:value={name}
					maxlength={200}
					required
					autocomplete="off"
					aria-invalid={fieldErrors.name ? true : undefined}
				/>
				{#if fieldErrors.name}<p class="text-[11px] text-danger-fg">{fieldErrors.name}</p>{/if}
			</div>
			<div class="space-y-1.5">
				<Label for="clone-profile-slug"
					>Slug <span class="font-normal text-muted-foreground">(optional)</span></Label
				>
				<Input
					id="clone-profile-slug"
					bind:value={slug}
					placeholder="derived from the name"
					autocomplete="off"
					spellcheck={false}
					class="font-mono text-xs"
					aria-invalid={slugInvalid || fieldErrors.slug ? true : undefined}
					aria-describedby="clone-profile-slug-help"
				/>
				<p
					id="clone-profile-slug-help"
					class="text-[11px] {slugInvalid || fieldErrors.slug
						? 'text-danger-fg'
						: 'text-muted-foreground'}"
				>
					{fieldErrors.slug ||
						(slugInvalid
							? 'Letters, digits, hyphens and underscores (at most 100).'
							: 'Identifies every version of the profile.')}
				</p>
			</div>
			{#if error}<p class="text-sm text-danger-fg" role="alert">{error}</p>{/if}
			<Dialog.Footer>
				<Button
					type="button"
					variant="outline"
					size="sm"
					disabled={busy}
					onclick={() => (open = false)}>Cancel</Button
				>
				<Button type="submit" size="sm" disabled={busy || !nameTrimmed || slugInvalid}>
					{#if busy}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
					{profile?.is_system ? 'Create my copy' : 'Clone'}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
