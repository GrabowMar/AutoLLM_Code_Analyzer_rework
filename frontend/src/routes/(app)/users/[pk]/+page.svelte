<script lang="ts">
	import { avatarColorClasses } from '$lib/constants/colors';
	import { page } from '$app/state';
	import { getMyActivity, getUser, type ActivityKind, type ApiUser, type UserActivity } from '$lib/api/users';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { getPreferences } from '$lib/stores/preferences.svelte';
	import { formatAgo } from '$lib/components/inference/endpoint-display';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import ProfileHero from '$lib/components/users-profile/ProfileHero.svelte';
	import KpiCard from '$lib/components/dashboard/KpiCard.svelte';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import { SETTINGS_SECTIONS } from '$lib/settings-sections';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import Microscope from '@lucide/svelte/icons/microscope';
	import FlaskConical from '@lucide/svelte/icons/flask-conical';
	import FileText from '@lucide/svelte/icons/file-text';
	import Activity from '@lucide/svelte/icons/activity';
	import AlertCircle from '@lucide/svelte/icons/alert-circle';
	import WandSparkles from '@lucide/svelte/icons/wand-sparkles';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import type { Component } from 'svelte';

	const auth = getAuth();
	const prefs = getPreferences();

	const KIND: Record<ActivityKind, { label: string; icon: Component }> = {
		application: { label: 'Application', icon: AppWindow },
		analysis: { label: 'Analysis', icon: Microscope },
		experiment: { label: 'Experiment', icon: FlaskConical },
		report: { label: 'Report', icon: FileText },
	};
	const SHORTCUTS = ['credentials', 'tokens', 'mcp', 'notifications'].map(
		(id) => SETTINGS_SECTIONS.find((s) => s.id === id)!,
	);

	let profile = $state<ApiUser | null>(null);
	let error = $state('');
	let loading = $state(true);
	let activity = $state<UserActivity | null>(null);
	let activityError = $state('');

	const isSelf = $derived(auth.user != null && profile != null && auth.user.email === profile.email);
	const avatarBg = $derived(isSelf ? (avatarColorClasses[prefs.avatarColor] ?? 'bg-blue-500') : 'bg-muted-foreground');
	const counts = $derived(activity?.counts ?? null);

	async function loadActivity() {
		activityError = '';
		try {
			activity = await getMyActivity();
		} catch {
			activityError = 'Could not load your activity.';
		}
	}

	async function load(pkParam: string | undefined) {
		loading = true;
		error = '';
		profile = null;
		activity = null;
		const pk = Number(pkParam);
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
		if (profile && auth.user?.email === profile.email) await loadActivity();
	}

	$effect(() => {
		void load(page.params.pk);
	});
</script>

<div class="space-y-4">
	{#if loading}
		<LoadingState label="loading profile" />
	{:else if error || !profile}
		<PageHeader title="Profile" />
		<EmptyState
			icon={AlertCircle}
			title={error || 'User not found'}
			description="Profiles are visible only to their owner."
		>
			{#snippet action()}
				<Button href="/" variant="outline" size="sm">Back to dashboard</Button>
			{/snippet}
		</EmptyState>
	{:else}
		<ProfileHero {profile} {isSelf} {avatarBg} memberSince={activity?.date_joined} />

		{#if isSelf}
			{#if activityError}
				<ErrorAlert message={activityError} onretry={loadActivity} />
			{/if}

			<section class="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="Your work">
				<KpiCard
					title="Applications"
					value={counts?.applications ?? null}
					subtitle={counts ? `${counts.applications_completed} completed` : 'generated apps'}
					icon={AppWindow}
					href="/applications"
				/>
				<KpiCard
					title="Analyses"
					value={counts?.analyses ?? null}
					subtitle="analysis runs"
					icon={Microscope}
					href="/analysis"
					accent="cyan"
				/>
				<KpiCard
					title="Experiments"
					value={counts?.experiments ?? null}
					subtitle="studies designed"
					icon={FlaskConical}
					href="/experiments"
					accent="violet"
				/>
				<KpiCard
					title="Reports"
					value={counts?.reports ?? null}
					subtitle="reports generated"
					icon={FileText}
					href="/reports"
					accent="amber"
				/>
			</section>

			<div class="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
				<Card.Root class="gap-0 overflow-clip py-0">
					<Card.Header class="border-b border-border py-3">
						<Card.Title class="flex items-center gap-2 text-base">
							<Activity class="size-4 text-muted-foreground" aria-hidden="true" />Recent activity
						</Card.Title>
					</Card.Header>
					<Card.Content class="p-0">
						{#if !activity && !activityError}
							<LoadingState label="loading activity" />
						{:else if !activity || activity.recent.length === 0}
							<EmptyState
								compact
								icon={WandSparkles}
								title="Nothing here yet"
								description="Applications, analyses, experiments and reports you create show up here."
							>
								{#snippet action()}
									<Button href="/sample-generator" size="sm"><WandSparkles />Generate an app</Button>
								{/snippet}
							</EmptyState>
						{:else}
							<ul class="divide-y divide-border">
								{#each activity.recent as item (item.kind + item.id)}
									{@const kind = KIND[item.kind]}
									<li>
										<a
											href={item.href}
											class="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
										>
											<kind.icon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
											<span class="min-w-0 flex-1">
												<span class="block truncate text-sm font-medium">{item.title}</span>
												<span class="block text-xs text-muted-foreground">
													{kind.label} · {formatAgo(item.created_at)}
												</span>
											</span>
											<StatusBadge status={item.status} />
										</a>
									</li>
								{/each}
							</ul>
						{/if}
					</Card.Content>
				</Card.Root>

				<div class="space-y-4">
					<Card.Root>
						<Card.Header>
							<Card.Title class="text-base">Account</Card.Title>
						</Card.Header>
						<Card.Content>
							<dl class="space-y-2.5 text-sm">
								<div class="flex justify-between gap-3">
									<dt class="text-muted-foreground">Role</dt>
									<dd>{profile.is_staff ? 'Staff' : 'Member'}</dd>
								</div>
								<div class="flex justify-between gap-3">
									<dt class="text-muted-foreground">Joined</dt>
									<dd>{activity ? new Date(activity.date_joined).toLocaleDateString() : '—'}</dd>
								</div>
								<div class="flex justify-between gap-3">
									<dt class="text-muted-foreground">Last sign-in</dt>
									<dd>{activity?.last_login ? formatAgo(activity.last_login) : '—'}</dd>
								</div>
							</dl>
						</Card.Content>
					</Card.Root>

					<Card.Root class="gap-0 py-0">
						<Card.Header class="border-b border-border py-3">
							<Card.Title class="text-base">Connections</Card.Title>
						</Card.Header>
						<Card.Content class="p-1.5">
							<ul>
								{#each SHORTCUTS as s (s.id)}
									<li>
										<a
											href="/users/settings#{s.id}"
											class="flex items-center gap-2.5 rounded-sm px-2.5 py-2 text-sm transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
										>
											<s.icon class="size-4 text-muted-foreground" aria-hidden="true" />
											<span class="flex-1">{s.label}</span>
											<ChevronRight class="size-4 text-muted-foreground" aria-hidden="true" />
										</a>
									</li>
								{/each}
							</ul>
						</Card.Content>
					</Card.Root>
				</div>
			</div>
		{/if}
	{/if}
</div>
