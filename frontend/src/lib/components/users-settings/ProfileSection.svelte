<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getMe, updateMe, type ApiUser } from '$lib/api/users';
	import { avatarColorClasses } from '$lib/constants/colors';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import { initialsFor } from '$lib/components/users-profile/initials';
	import { Input } from '$lib/components/ui/input';
	import SettingsBlock from './SettingsBlock.svelte';
	import SettingsRow from './SettingsRow.svelte';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';
	import ArrowUpRight from '@lucide/svelte/icons/arrow-up-right';
	import Lock from '@lucide/svelte/icons/lock';

	const auth = getAuth();
	const prefs = getPreferences();

	let profile = $state<ApiUser | null>(null);
	let loading = $state(true);
	let loadError = $state('');
	let name = $state('');
	let error = $state('');
	let saving = $state(false);

	const dirty = $derived(profile != null && name.trim() !== profile.name);
	const initials = $derived(initialsFor(name.trim() || profile?.name, profile?.email ?? ''));

	async function load() {
		loading = true;
		loadError = '';
		try {
			profile = await getMe();
			name = profile.name;
			// The session payload may lack the display name; keep the header and rail in step.
			if (profile.name && auth.user?.name !== profile.name) auth.setDisplayName(profile.name);
		} catch {
			loadError = 'Could not load your profile.';
		} finally {
			loading = false;
		}
	}

	async function save(e: Event) {
		e.preventDefault();
		if (!dirty) return;
		error = '';
		saving = true;
		try {
			profile = await updateMe({ name: name.trim() });
			name = profile.name;
			auth.setDisplayName(profile.name);
			toast.success('Profile saved.');
		} catch {
			error = 'Could not save your profile.';
		} finally {
			saving = false;
		}
	}

	onMount(load);
</script>

{#if loading}
	<LoadingState label="loading profile" />
{:else if loadError || !profile}
	<ErrorAlert message={loadError || 'Could not load your profile.'} onretry={load} />
{:else}
	<form onsubmit={save}>
		<SettingsBlock>
			<SettingsRow label="Avatar" description="Your initials on a color you pick under Appearance.">
				<div class="flex items-center gap-3">
					<div
						class="flex size-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold text-white {avatarColorClasses[
							prefs.avatarColor
						] ?? 'bg-blue-500'}"
						aria-hidden="true"
					>
						{initials}
					</div>
					<Button href="#appearance" variant="outline" size="sm">Change color</Button>
				</div>
			</SettingsRow>

			<SettingsRow label="Display name" for="profile-name" description="Shown in the header, on your profile and on what you create.">
				<Input
					id="profile-name"
					class="sm:w-72"
					bind:value={name}
					placeholder="Your name"
					maxlength={255}
					autocomplete="name"
				/>
			</SettingsRow>

			<SettingsRow label="Email" description="Your sign-in address. Ask an administrator to change it.">
				<p class="flex items-center gap-2 text-sm sm:w-72">
					<Lock class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
					<span class="truncate">{profile.email}</span>
				</p>
			</SettingsRow>

			{#if error}<div class="settings-row"><ErrorAlert message={error} /></div>{/if}

			{#snippet footer()}
				{#if auth.user}
					<a href="/users/{auth.user.id}" class="inline-flex items-center gap-1 text-[13px] settings-link">
						View your public profile<ArrowUpRight class="size-3.5" aria-hidden="true" />
					</a>
				{:else}
					<span></span>
				{/if}
				<div class="flex gap-2">
					{#if dirty}
						<Button type="button" variant="ghost" size="sm" onclick={() => (name = profile!.name)} disabled={saving}>
							Discard
						</Button>
					{/if}
					<Button type="submit" size="sm" disabled={!dirty || saving} aria-busy={saving}>
						{#if saving}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
						Save
					</Button>
				</div>
			{/snippet}
		</SettingsBlock>
	</form>
{/if}
