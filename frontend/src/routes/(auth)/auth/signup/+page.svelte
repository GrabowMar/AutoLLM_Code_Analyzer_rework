<script lang="ts">
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import Eye from '@lucide/svelte/icons/eye';
	import EyeOff from '@lucide/svelte/icons/eye-off';
	import Loader2 from '@lucide/svelte/icons/loader-2';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { formatApiError } from '$lib/api/core';
	import { getAuth } from '$lib/stores/auth.svelte';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import { Button } from '$lib/components/ui/button';
	import ErrorAlert from '$lib/components/ErrorAlert.svelte';

	const auth = getAuth();

	let bootstrapRequired = $state(false);
	let bootstrapStatusLoading = $state(true);
	let bootstrapLoginEmail = $state('');
	let name = $state('');
	let email = $state('');
	let password = $state('');
	let password2 = $state('');
	let error = $state('');
	let submitting = $state(false);
	let showPassword = $state(false);
	let showConfirmation = $state(false);
	let confirmationInput = $state<HTMLInputElement>();

	$effect(() => {
		// Clear a mismatch as soon as the user corrects either field.
		if (password === password2 && error === 'Passwords do not match.') error = '';
	});

	onMount(async () => {
		try {
			const status = await auth.getBootstrapStatus();
			bootstrapRequired = status.requiresBootstrap;
			bootstrapLoginEmail = status.defaultEmail;
		} catch (err: unknown) {
			error = formatApiError(err, 'Could not load sign up options.');
		} finally {
			bootstrapStatusLoading = false;
		}
	});

	$effect(() => {
		if (!auth.isLoading && auth.isAuthenticated) {
			goto('/');
		}
	});

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';

		if (password !== password2) {
			error = 'Passwords do not match.';
			confirmationInput?.focus();
			return;
		}

		submitting = true;
		try {
			const res = bootstrapRequired
				? await auth.bootstrapAdminSignup(name, password)
				: await auth.signup(email, password, password2);
			if (!res.ok) {
				error = res.error || 'Sign up failed. Please try again.';
				return;
			}
			if (auth.isAuthenticated) {
				goto('/');
			} else {
				goto('/auth/verify-email?created=1');
			}
		} catch (err: unknown) {
			error = formatApiError(err, 'Sign up failed. Please try again.');
		} finally {
			submitting = false;
		}
	}
</script>

<svelte:head>
	<title>Sign Up · LLM Lab</title>
</svelte:head>

<div class="signup-heading">
	<p class="signup-eyebrow">Your research workspace</p>
	<h1>{bootstrapRequired ? 'Set up your lab.' : 'A little curiosity.\nA lot of possibility.'}</h1>
	<p class="signup-description">
		{#if bootstrapRequired}
			Create the first administrator account to get started.
		{:else}
			Create your account. See what your models can do.
		{/if}
	</p>
</div>

<div class="signup-fields">
	{#if error}
		<ErrorAlert message={error} class="mb-5" />
	{/if}
	{#if bootstrapStatusLoading}
		<div class="signup-loading"><LoadingState label="loading sign up options" class="py-8" /></div>
	{:else}
		<form onsubmit={handleSubmit} class="space-y-5" aria-busy={submitting}>
			{#if bootstrapRequired}
				<div class="space-y-2">
					<Label for="name">Admin name</Label>
					<Input id="name" type="text" placeholder="Administrator" bind:value={name} required autocomplete="name" class="h-12 text-base" aria-describedby="admin-email-note" />
					<p id="admin-email-note" class="field-help">Your login email will be <span class="break-all font-medium text-foreground">{bootstrapLoginEmail}</span>.</p>
				</div>
			{:else}
				<div class="space-y-2">
					<Label for="email">Email address</Label>
					<Input id="email" type="email" placeholder="you@example.com" bind:value={email} required autocomplete="email" class="h-12 text-base" />
				</div>
			{/if}
			<div class="space-y-2">
				<Label for="password">Password</Label>
				<div class="password-field">
					<Input id="password" type={showPassword ? 'text' : 'password'} bind:value={password} required autocomplete="new-password" class="h-12 pr-12 text-base" placeholder="Choose a password" />
					<button type="button" class="password-toggle" onclick={() => showPassword = !showPassword} aria-label={showPassword ? 'Hide password' : 'Show password'} aria-pressed={showPassword}>
						{#if showPassword}<EyeOff aria-hidden="true" />{:else}<Eye aria-hidden="true" />{/if}
					</button>
				</div>
			</div>
			<div class="space-y-2">
				<Label for="password2">Confirm password</Label>
				<div class="password-field">
					<input bind:this={confirmationInput} id="password2" type={showConfirmation ? 'text' : 'password'} bind:value={password2} required autocomplete="new-password" placeholder="Once more, to be sure" class="confirmation-input" aria-invalid={error === 'Passwords do not match.'} aria-describedby={error === 'Passwords do not match.' ? 'password-mismatch' : undefined} />
					<button type="button" class="password-toggle" onclick={() => showConfirmation = !showConfirmation} aria-label={showConfirmation ? 'Hide confirmed password' : 'Show confirmed password'} aria-pressed={showConfirmation}>
						{#if showConfirmation}<EyeOff aria-hidden="true" />{:else}<Eye aria-hidden="true" />{/if}
					</button>
				</div>
				{#if error === 'Passwords do not match.'}<p id="password-mismatch" class="text-sm text-destructive">Passwords do not match.</p>{/if}
			</div>
			<Button type="submit" class="h-12 w-full gap-3 text-base" disabled={submitting}>
				{#if submitting}<Loader2 class="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />{/if}
				{submitting ? 'Creating account…' : bootstrapRequired ? 'Create admin account' : 'Create account'}
				{#if !submitting}<ArrowRight class="size-4" aria-hidden="true" />{/if}
			</Button>
			<p class="email-note"><ShieldCheck aria-hidden="true" />{bootstrapRequired ? 'Your workspace. Your research.' : 'We’ll email you a link to verify your account.'}</p>
		</form>
	{/if}
</div>

<p class="signin-note">Already have an account? <a href="/auth/login">Log in <ArrowRight aria-hidden="true" /></a></p>

<style>
	.signup-eyebrow { font: 11px var(--font-mono); text-transform: uppercase; letter-spacing: .13em; color: var(--primary); }
	h1 { margin-top: 16px; font: 500 clamp(32px, 2.8vw, 40px)/1.15 var(--font-display); letter-spacing: -.035em; white-space: pre-line; text-wrap: balance; }
	.signup-description { margin-top: 14px; font-size: 14px; line-height: 1.6; color: var(--muted-foreground); }
	.signup-fields { margin-top: 30px; }
	.signup-loading { min-height: 365px; }
	.password-field { position: relative; }
	.password-toggle { position: absolute; right: 2px; top: 2px; display: flex; align-items: center; justify-content: center; width: 44px; height: 44px; color: var(--muted-foreground); border-radius: 4px; cursor: pointer; transition: color 150ms, background-color 150ms; }
	.password-toggle:hover { color: var(--foreground); background: var(--accent); }
	.password-toggle:focus-visible { outline: 2px solid var(--ring); outline-offset: -3px; }
	.password-toggle :global(svg) { width: 17px; height: 17px; }
	.confirmation-input { width: 100%; height: 48px; border: 1px solid var(--input); border-radius: var(--radius); padding: 4px 48px 4px 12px; background: var(--surface-1); font-size: 16px; transition: border-color 150ms, box-shadow 150ms; }
	.confirmation-input::placeholder { color: var(--muted-foreground); opacity: .7; }
	.confirmation-input:hover { border-color: color-mix(in oklab, var(--primary) 40%, var(--input)); }
	.confirmation-input:focus-visible { outline: none; border-color: var(--ring); box-shadow: 0 0 0 2px color-mix(in oklab, var(--ring) 30%, transparent); }
	.confirmation-input[aria-invalid="true"] { border-color: var(--destructive); }
	.field-help { color: var(--muted-foreground); font-size: 12px; line-height: 1.6; }
	.email-note { display: flex; align-items: flex-start; justify-content: center; gap: 7px; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; }
	.email-note :global(svg) { flex-shrink: 0; width: 14px; height: 14px; margin-top: 2px; }
	.signin-note { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 6px; border-top: 1px solid var(--border); margin-top: 28px; padding-top: 20px; color: var(--muted-foreground); font-size: 13px; }
	.signin-note a { display: inline-flex; min-height: 32px; align-items: center; gap: 5px; color: var(--primary); font-weight: 500; text-underline-offset: 4px; }
	.signin-note a:hover { text-decoration: underline; }
	.signin-note a:focus-visible { outline: 2px solid var(--ring); outline-offset: 4px; }
	.signin-note :global(svg) { width: 13px; height: 13px; }
</style>
