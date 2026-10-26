<script lang="ts">
	import type { ApiUser } from '$lib/api/users';
	import { Button } from '$lib/components/ui/button';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import { initialsFor } from './initials';
	import Pencil from '@lucide/svelte/icons/pencil';

	interface Props {
		profile: ApiUser;
		isSelf: boolean;
		avatarBg: string;
		memberSince?: string | null;
	}

	let { profile, isSelf, avatarBg, memberSince = null }: Props = $props();

	const initials = $derived(initialsFor(profile.name, profile.email));
</script>

<PageHeader title={profile.name || profile.email} documentTitle="Profile">
	{#snippet leading()}
		<div
			class="flex size-9 shrink-0 items-center justify-center rounded-md font-mono text-sm font-semibold text-white {avatarBg}"
			aria-hidden="true"
		>
			{initials}
		</div>
	{/snippet}
	{#snippet meta()}
		<StatusBadge status={profile.is_staff ? 'staff' : 'member'} tone={profile.is_staff ? 'info' : 'neutral'} />
	{/snippet}
	<p class="flex flex-wrap gap-x-3 gap-y-0.5 text-muted-foreground">
		{#if profile.name}<span class="font-mono">{profile.email}</span>{/if}
		{#if memberSince}
			<span>
				Member since {new Date(memberSince).toLocaleDateString(undefined, { year: 'numeric', month: 'long' })}
			</span>
		{/if}
	</p>
	{#snippet actions()}
		{#if isSelf}
			<Button href="/users/settings#account" size="sm" variant="outline"><Pencil />Edit profile</Button>
		{/if}
	{/snippet}
</PageHeader>
