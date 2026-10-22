<script lang="ts">
	import { avatarColorClasses } from '$lib/constants/colors';
	import { page } from '$app/state';
	import { getUser, type ApiUser } from '$lib/api/users';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import * as Card from '$lib/components/ui/card';
	import ProfileHero from '$lib/components/users-profile/ProfileHero.svelte';
	import ProfileStats from '$lib/components/users-profile/ProfileStats.svelte';
	import * as Tabs from '$lib/components/ui/tabs';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import Activity from '@lucide/svelte/icons/activity';
	import Award from '@lucide/svelte/icons/award';
	import Inbox from '@lucide/svelte/icons/inbox';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import { onMount } from 'svelte';

	const auth = getAuth();
	const prefs = getPreferences();

	let profile = $state<ApiUser | null>(null);
	let error = $state('');
	let loading = $state(true);

	let activeTab = $state('overview');

	let isSelf = $derived(
		auth.user != null && profile != null && auth.user.email === profile.email,
	);

	let avatarBg = $derived(isSelf ? avatarColorClasses[prefs.avatarColor] ?? 'bg-blue-500' : 'bg-muted-foreground');

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

<div class="mx-auto max-w-5xl space-y-6">
	{#if loading}
		<LoadingState label="loading profile" />
	{:else if error || !profile}
		<PageHeader title="User" />
		<EmptyState
			icon={AlertCircle}
			title={error || 'User not found'}
			description="The profile you're looking for is unavailable."
		>
			{#snippet action()}
				<Button href="/" variant="outline" size="sm">Back to dashboard</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<ProfileHero {profile} {isSelf} {avatarBg} />

		<ProfileStats stats={{ applications: null, analyses: null, reports: null, rank: null }} />

		<Tabs.Root bind:value={activeTab} class="space-y-4">
			<Tabs.List aria-label="Profile sections">
				<Tabs.Trigger value="overview"><LayoutGrid />Overview</Tabs.Trigger>
				<Tabs.Trigger value="activity"><Activity />Activity</Tabs.Trigger>
				<Tabs.Trigger value="achievements"><Award />Achievements</Tabs.Trigger>
			</Tabs.List>

			<Tabs.Content value="overview">
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
								<span class="font-medium truncate font-mono">{profile.email}</span>
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
							<EmptyState
								compact
								icon={Inbox}
								title="No highlights yet"
								description="When this user contributes analyses or reports, they'll appear here."
							/>
						</Card.Content>
					</Card.Root>
				</div>
			</Tabs.Content>
			<Tabs.Content value="activity">
				<Card.Root>
					<Card.Content>
						<EmptyState
							compact
							icon={Activity}
							title="No recent activity"
							description="A feed of analyses, reports and runs will show up here."
						/>
					</Card.Content>
				</Card.Root>
			</Tabs.Content>
			<Tabs.Content value="achievements">
				<Card.Root>
					<Card.Content>
						<EmptyState
							compact
							icon={Award}
							title="No achievements unlocked"
							description="Complete analyses and reach milestones to earn badges."
						/>
					</Card.Content>
				</Card.Root>
			</Tabs.Content>
		</Tabs.Root>
	{/if}
</div>
