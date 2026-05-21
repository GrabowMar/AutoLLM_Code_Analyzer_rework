<script lang="ts">
	import { changePassword } from '$lib/api/client';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Button } from '$lib/components/ui/button';
	import { Alert, AlertDescription } from '$lib/components/ui/alert';

	interface Props {}

	let {}: Props = $props();

	let currentPassword = $state('');
	let newPassword = $state('');
	let newPassword2 = $state('');
	let passwordError = $state('');
	let passwordSuccess = $state('');
	let passwordSubmitting = $state(false);

	async function handlePasswordChange(e: Event) {
		e.preventDefault();
		passwordError = '';
		passwordSuccess = '';

		if (newPassword !== newPassword2) {
			passwordError = 'Passwords do not match.';
			return;
		}

		passwordSubmitting = true;
		try {
			await changePassword({ current_password: currentPassword, new_password: newPassword });
			passwordSuccess = 'Password changed successfully.';
			currentPassword = '';
			newPassword = '';
			newPassword2 = '';
		} catch (err: unknown) {
			const e = err as { errors?: Array<{ message: string }> };
			if (e.errors?.length) {
				passwordError = e.errors.map((x) => x.message).join('. ');
			} else {
				passwordError = 'Failed to change password.';
			}
		} finally {
			passwordSubmitting = false;
		}
	}
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>Change Password</Card.Title>
		<Card.Description>Update your password.</Card.Description>
	</Card.Header>
	<Card.Content>
		{#if passwordSuccess}
			<Alert class="mb-4">
				<AlertDescription>{passwordSuccess}</AlertDescription>
			</Alert>
		{/if}
		{#if passwordError}
			<Alert variant="destructive" class="mb-4">
				<AlertDescription>{passwordError}</AlertDescription>
			</Alert>
		{/if}
		<form onsubmit={handlePasswordChange} class="space-y-4">
			<div class="space-y-2">
				<Label for="current-password">Current Password</Label>
				<Input
					id="current-password"
					type="password"
					bind:value={currentPassword}
					required
					autocomplete="current-password"
				/>
			</div>
			<div class="space-y-2">
				<Label for="new-password">New Password</Label>
				<Input
					id="new-password"
					type="password"
					bind:value={newPassword}
					required
					autocomplete="new-password"
				/>
			</div>
			<div class="space-y-2">
				<Label for="new-password2">Confirm New Password</Label>
				<Input
					id="new-password2"
					type="password"
					bind:value={newPassword2}
					required
					autocomplete="new-password"
				/>
			</div>
			<Button type="submit" disabled={passwordSubmitting}>
				{passwordSubmitting ? 'Changing...' : 'Change Password'}
			</Button>
		</form>
	</Card.Content>
</Card.Root>
