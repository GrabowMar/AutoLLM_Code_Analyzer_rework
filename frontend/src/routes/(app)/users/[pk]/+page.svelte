<script lang="ts">
	import { page } from '$app/state';
	import { getUser, type ApiUser } from '$lib/api/users';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import * as Card from '$lib/components/ui/card';
	import ProfileHero from '$lib/components/users-profile/ProfileHero.svelte';
	import ProfileStats from '$lib/components/users-profile/ProfileStats.svelte';
	import ProfileTabs, { type ProfileTab } from '$lib/components/users-profile/ProfileTabs.svelte';
	import ProfileEmptyState from '$lib/components/users-profile/ProfileEmptyState.svelte';
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import Activity from '@lucide/svelte/icons/activity';
	import Award from '@lucide/svelte/icons/award';
	import Inbox from '@lucide/svelte/icons/inbox';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import { onMount } from 'svelte';

	const auth = getAuth();
	const prefs = getPreferences();

	const avatarColorMap: Record<string, string> = {
		blue: 'bg-blue-500',
		indigo: 'bg-indigo-500',
		purple: 'bg-purple-500',
		pink: 'bg-pink-500',
		red: 'bg-red-500',
		orange: 'bg-orange-500',
		amber: 'bg-amber-500',
		green: 'bg-green-500',
		teal: 'bg-teal-500',
		cyan: 'bg-cyan-500',
	};

	let profile = $state<ApiUser | null>(null);
	let error = $state('');
	let loading = $state(true);

	const tabs: ProfileTab[] = [
		{ id: 'overview', label: 'Overview', icon: LayoutGrid },
		{ id: 'activity', label: 'Activity', icon: Activity },
		{ id: 'achievements', label: 'Achievements', icon: Award },
	];
	let activeTab = $state('overview');

	let isSelf = $derived(
		auth.user != null && profile != null && auth.user.email === profile.email,
	);

	let avatarBg = $derived(isSelf ? avatarColorMap[prefs.avatarColor] ?? 'bg-blue-500' : 'bg-slate-500');

	onMount(async () => {
		const pk = Number(page.params.pk);
		if (!Number.isFinite(pk)) {
			error = 'Invalid user id.';
			loading = false;
			return;
		}
		try {
			profile = await getUser(pk);
		} catch {
			error = 'User not found.';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>{profile?.name || profile?.email || 'User'} - LLM Lab</title>
</svelte:head>

<div class="mx-auto max-w-5xl space-y-5">
	{#if loading}
		<div class="space-y-5 animate-pulse motion-reduce:animate-none">
			<div class="h-44 rounded-xl bg-muted"></div>
			<div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
				{#each Array(4) as _}
					<div class="h-20 rounded-lg bg-muted"></div>
				{/each}
			</div>
			<div class="h-64 rounded-lg bg-muted"></div>
		</div>
	{:else if error || !profile}
		<Card.Root>
			<Card.Content class="flex flex-col items-center justify-center gap-3 pt-10 pb-10 text-center">
				<div class="flex h-10 w-10 items-center justify-center rounded-full bg-muted text-muted-foreground">
					<AlertCircle class="h-5 w-5" />
				</div>
				<div>
					<p class="text-sm font-medium">{error || 'User not found'}</p>
					<p class="mt-1 text-xs text-muted-foreground">The profile you're looking for is unavailable.</p>
				</div>
			</Card.Content>
		</Card.Root>
	{:else}
		<ProfileHero {profile} {isSelf} {avatarBg} />

		<ProfileStats stats={{ applications: null, analyses: null, reports: null, rank: null }} />

		<ProfileTabs {tabs} active={activeTab} onSelect={(id) => (activeTab = id)} />

		<div class="pt-2">
			{#if activeTab === 'overview'}
				<div class="grid gap-4 md:grid-cols-2">
					<Card.Root>
						<Card.Header>
							<Card.Title class="text-base">About</Card.Title>
							<Card.Description>Public information for this user.</Card.Description>
						</Card.Header>
						<Card.Content class="space-y-3 text-sm">
							<div class="flex items-baseline justify-between gap-2 border-b border-border/60 pb-2">
								<span class="text-muted-foreground">Display name</span>
								<span class="font-medium">{profile.name || '—'}</span>
							</div>
							<div class="flex items-baseline justify-between gap-2 border-b border-border/60 pb-2">
								<span class="text-muted-foreground">Email</span>
								<span class="font-medium truncate" style="font-family: var(--font-mono);">{profile.email}</span>
							</div>
							<div class="flex items-baseline justify-between gap-2">
								<span class="text-muted-foreground">Role</span>
								<span class="font-medium">{profile.is_staff ? 'Staff' : 'Member'}</span>
							</div>
						</Card.Content>
					</Card.Root>

					<Card.Root>
						<Card.Header>
							<Card.Title class="text-base">Highlights</Card.Title>
							<Card.Description>Recent contribution snapshot.</Card.Description>
						</Card.Header>
						<Card.Content>
							<ProfileEmptyState
								icon={Inbox}
								title="No highlights yet"
								description="When this user contributes analyses or reports, they'll appear here."
							/>
						</Card.Content>
					</Card.Root>
				</div>
			{:else if activeTab === 'activity'}
				<Card.Root>
					<Card.Content class="pt-6">
						<ProfileEmptyState
							icon={Activity}
							title="No recent activity"
							description="A feed of analyses, reports and runs will show up here."
						/>
					</Card.Content>
				</Card.Root>
			{:else if activeTab === 'achievements'}
				<Card.Root>
					<Card.Content class="pt-6">
						<ProfileEmptyState
							icon={Award}
							title="No achievements unlocked"
							description="Complete analyses and reach milestones to earn badges."
						/>
					</Card.Content>
				</Card.Root>
			{/if}
		</div>
	{/if}
</div>
