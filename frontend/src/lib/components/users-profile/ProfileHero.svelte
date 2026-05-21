<script lang="ts">
	import type { ApiUser } from '$lib/api/users';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import Mail from '@lucide/svelte/icons/mail';
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

<section
	class="relative overflow-hidden rounded-xl border border-border bg-card"
>
	<!-- Gradient banner -->
	<div
		class="h-28 sm:h-32 w-full"
		style="background:
			radial-gradient(120% 80% at 0% 0%, color-mix(in oklch, var(--primary) 35%, transparent), transparent 60%),
			radial-gradient(120% 80% at 100% 100%, color-mix(in oklch, var(--accent) 45%, transparent), transparent 60%),
			linear-gradient(135deg, color-mix(in oklch, var(--primary) 15%, var(--card)), var(--card));"
		aria-hidden="true"
	></div>

	<div class="px-4 pb-5 sm:px-6 sm:pb-6">
		<div class="-mt-10 flex flex-col gap-4 sm:-mt-12 sm:flex-row sm:items-end sm:justify-between">
			<div class="flex flex-col items-start gap-3 sm:flex-row sm:items-end">
				<div
					class={'flex h-20 w-20 sm:h-24 sm:w-24 items-center justify-center rounded-2xl text-2xl sm:text-3xl font-bold text-white ring-4 ring-background shadow-lg ' +
						avatarBg}
					style="font-family: var(--font-mono);"
				>
					{initials}
				</div>
				<div class="space-y-1 pb-1">
					<h1 class="text-xl sm:text-2xl font-semibold tracking-tight">
						{profile.name || 'Unnamed user'}
					</h1>
					<div class="flex items-center gap-1.5 text-sm text-muted-foreground">
						<Mail class="h-3.5 w-3.5" />
						<span style="font-family: var(--font-mono);" class="truncate">{profile.email}</span>
					</div>
					<div class="flex flex-wrap items-center gap-1.5 pt-1">
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
					</div>
				</div>
			</div>

			{#if isSelf}
				<div class="flex shrink-0 gap-2">
					<Button href="/users/settings#profile" size="sm" variant="outline">
						<Pencil class="h-3.5 w-3.5" />
						Edit profile
					</Button>
				</div>
			{/if}
		</div>
	</div>
</section>
