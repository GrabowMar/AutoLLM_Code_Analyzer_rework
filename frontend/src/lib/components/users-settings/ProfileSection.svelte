<script lang="ts">
	import { updateMe } from '$lib/api/client';
	import type { ApiUser } from '$lib/api/client';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	interface Props {
		profile: ApiUser | null;
		onUpdated: (user: ApiUser) => void;
	}

	let { profile, onUpdated }: Props = $props();

	let name = $state('');
	let profileError = $state('');
	let profileSuccess = $state('');
	let profileSubmitting = $state(false);

	$effect(() => {
		if (profile) name = profile.name;
	});

	async function handleProfileUpdate(e: Event) {
		e.preventDefault();
		profileError = '';
		profileSuccess = '';
		profileSubmitting = true;
		try {
			const updated = await updateMe({ name });
			onUpdated(updated);
			profileSuccess = 'Profile updated successfully.';
		} catch {
			profileError = 'Failed to update profile.';
		} finally {
			profileSubmitting = false;
		}
	}
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Account Information</Card.Title>
		<Card.Description>Update your personal information.</Card.Description>
	</Card.Header>
	<Card.Content>
		{#if profileSuccess}
			<Alert class="mb-4">
				<AlertDescription>{profileSuccess}</AlertDescription>
			</Alert>
		{/if}
		{#if profileError}
			<Alert variant="destructive" class="mb-4">
				<AlertDescription>{profileError}</AlertDescription>
			</Alert>
		{/if}
		<form onsubmit={handleProfileUpdate} class="space-y-4">
			<div class="space-y-2">
				<Label>Email</Label>
				<div class="inline-flex items-center rounded-md border border-border bg-muted/50 px-3 py-1.5 text-sm text-muted-foreground">
					{profile?.email ?? '—'}
				</div>
			</div>
			<div class="space-y-2">
				<Label for="name">Name</Label>
				<Input id="name" type="text" bind:value={name} placeholder="Your name" />
			</div>
			<Button type="submit" disabled={profileSubmitting}>
				{profileSubmitting ? 'Saving...' : 'Save Changes'}
			</Button>
		</form>
	</Card.Content>
</Card.Root>
