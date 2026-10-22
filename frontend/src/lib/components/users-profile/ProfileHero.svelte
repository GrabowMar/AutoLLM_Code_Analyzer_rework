<script lang="ts">
	import type { ApiUser } from '$lib/api/users';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Shield from '@lucide/svelte/icons/shield';
	import UserIcon from '@lucide/svelte/icons/user';
	import Pencil from '@lucide/svelte/icons/pencil';

	interface Props {
		profile: ApiUser;
		isSelf: boolean;
		avatarBg: string;
	}

	let { profile, isSelf, avatarBg }: Props = $props();

	let initials = $derived.by(() => {
		const source = profile.name?.trim() || profile.email;
		const parts = source.split(/\s+/).filter(Boolean);
		if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
		return source.slice(0, 2).toUpperCase();
	});
</script>

<div class="flex items-start gap-4">
	<div
		class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl font-mono text-xl font-bold text-white sm:h-16 sm:w-16 sm:text-2xl {avatarBg}"
		aria-hidden="true"
	>
		{initials}
	</div>
	<div class="min-w-0 flex-1">
		<PageHeader title={profile.name || 'Unnamed user'} description={profile.email}>
			{#snippet meta()}
				{#if profile.is_staff}
					<Badge variant="default" class="gap-1">
						<Shield class="h-3 w-3" />
						Staff
					</Badge>
				{/if}
				<Badge variant="secondary" class="gap-1">
					<UserIcon class="h-3 w-3" />
					Member
				</Badge>
			{/snippet}
			{#snippet actions()}
				{#if isSelf}
					<Button href="/users/settings#profile" size="sm" variant="outline">
						<Pencil class="h-3.5 w-3.5" />
						Edit profile
					</Button>
				{/if}
			{/snippet}
		</PageHeader>
	</div>
</div>
