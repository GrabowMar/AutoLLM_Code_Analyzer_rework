<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { changePassword } from '$lib/api/users';
	import { Input } from '$lib/components/ui/input';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Button } from '$lib/components/ui/button';
	import SettingsBlock from './SettingsBlock.svelte';
	import SettingsRow from './SettingsRow.svelte';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	const MIN_LENGTH = 8;

	let currentPassword = $state('');
	let newPassword = $state('');
	let confirmPassword = $state('');
	let reveal = $state(false);
	let error = $state('');
	let submitting = $state(false);

	const tooShort = $derived(newPassword.length > 0 && newPassword.length < MIN_LENGTH);
	const mismatch = $derived(confirmPassword.length > 0 && newPassword !== confirmPassword);
	const canSubmit = $derived(
		currentPassword.length > 0 && newPassword.length >= MIN_LENGTH && newPassword === confirmPassword && !submitting,
	);

	async function submit(e: Event) {
		e.preventDefault();
		if (!canSubmit) return;
		error = '';
		submitting = true;
		try {
			await changePassword({ current_password: currentPassword, new_password: newPassword });
			toast.success('Password changed.');
			currentPassword = '';
			newPassword = '';
			confirmPassword = '';
		} catch (err: unknown) {
			const body = err as { errors?: Array<{ message: string }> };
			error = body.errors?.length ? body.errors.map((x) => x.message).join(' ') : 'Could not change the password.';
		} finally {
			submitting = false;
		}
	}
</script>

<form onsubmit={submit}>
	<SettingsBlock
		title="Password"
		description="Use at least {MIN_LENGTH} characters you don't use elsewhere. Other signed-in browsers stay signed in."
	>
		<SettingsRow label="Current password" for="current-password">
			<Input
				id="current-password"
				class="sm:w-72"
				type={reveal ? 'text' : 'password'}
				bind:value={currentPassword}
				required
				autocomplete="current-password"
			/>
		</SettingsRow>

		<SettingsRow label="New password" for="new-password">
			<div class="space-y-1.5 sm:w-72">
				<Input
					id="new-password"
					type={reveal ? 'text' : 'password'}
					bind:value={newPassword}
					required
					minlength={MIN_LENGTH}
					autocomplete="new-password"
					aria-invalid={tooShort || undefined}
					aria-describedby="new-password-hint"
				/>
				<div class="h-1 overflow-hidden rounded-full bg-muted" aria-hidden="true">
					<div
						class="h-full rounded-full transition-[width,background-color] duration-200 motion-reduce:transition-none {newPassword.length >=
						MIN_LENGTH
							? 'bg-success'
							: 'bg-warning'}"
						style="width: {Math.min(100, (newPassword.length / MIN_LENGTH) * 100)}%"
					></div>
				</div>
				<p id="new-password-hint" class="text-xs {tooShort ? 'text-warning-fg' : 'text-muted-foreground'}">
					{#if tooShort}
						{MIN_LENGTH - newPassword.length} more characters needed
					{:else if newPassword.length >= MIN_LENGTH}
						Long enough
					{:else}
						At least {MIN_LENGTH} characters
					{/if}
				</p>
			</div>
		</SettingsRow>

		<SettingsRow label="Confirm new password" for="confirm-password">
			<div class="space-y-1.5 sm:w-72">
				<Input
					id="confirm-password"
					type={reveal ? 'text' : 'password'}
					bind:value={confirmPassword}
					required
					autocomplete="new-password"
					aria-invalid={mismatch || undefined}
					aria-describedby={mismatch ? 'confirm-password-hint' : undefined}
				/>
				{#if mismatch}
					<p id="confirm-password-hint" class="text-xs text-danger-fg">Passwords do not match</p>
				{/if}
			</div>
		</SettingsRow>

		{#if error}<div class="settings-row"><ErrorAlert message={error} /></div>{/if}

		{#snippet footer()}
			<label class="inline-flex cursor-pointer items-center gap-2 text-[13px] text-muted-foreground">
				<Checkbox bind:checked={reveal} />Show passwords
			</label>
			<Button type="submit" size="sm" disabled={!canSubmit} aria-busy={submitting}>
				{#if submitting}<LoaderCircle class="animate-spin motion-reduce:animate-none" />{/if}
				Update password
			</Button>
		{/snippet}
	</SettingsBlock>
</form>
