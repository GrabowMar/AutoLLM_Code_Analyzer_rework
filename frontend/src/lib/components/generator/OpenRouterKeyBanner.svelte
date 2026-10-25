<script lang="ts" module>
	/**
	 * loading · ok (own key) · fallback (deployment key) · missing · invalid ·
	 * unknown (status request failed — never reported as "missing").
	 */
	export type OpenRouterKeyState = 'loading' | 'ok' | 'fallback' | 'missing' | 'invalid' | 'unknown';
</script>

<script lang="ts">
	/**
	 * OpenRouterKeyBanner — whether runs can call OpenRouter, and how.
	 *
	 * Props:
	 * - `hideWhenOk` (default false): render nothing when the user's own key or the
	 *   deployment fallback key will be used.
	 * - `keyState` (bindable, output): the resolved {@link OpenRouterKeyState}.
	 * - `involved` (default true): whether any OpenRouter model takes part. With
	 *   false (every selected model runs on a local endpoint) nothing renders, the
	 *   status is not fetched and `keyState` reports 'ok' (no key is needed).
	 * - `class`.
	 * A failed status request shows a neutral note with Retry, never "key required";
	 * the server preflight (preview `missing_api_key`) stays authoritative.
	 */
	import { getOpenRouterCredentialStatus, type CredentialStatus } from '$lib/api/credentials';
	import { toneClasses } from '$lib/constants/colors';
	import { cn } from '$lib/utils';
	import { Button } from '$lib/components/ui/button';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import Info from '@lucide/svelte/icons/info';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		hideWhenOk?: boolean;
		keyState?: OpenRouterKeyState;
		involved?: boolean;
		class?: string;
	}

	let {
		hideWhenOk = false,
		keyState = $bindable('loading'),
		involved = true,
		class: className,
	}: Props = $props();

	const SETTINGS_HREF = '/users/settings#credentials';

	let status = $state<CredentialStatus | null>(null);
	let loading = $state(true);
	let failed = $state(false);

	let requested = false;

	const resolved = $derived.by((): OpenRouterKeyState => {
		if (!involved) return 'ok';
		if (loading) return 'loading';
		if (failed || !status) return 'unknown';
		if (status.configured) return status.last_validation_status === 'invalid' ? 'invalid' : 'ok';
		return status.global_fallback_available ? 'fallback' : 'missing';
	});

	$effect(() => {
		keyState = resolved;
	});

	async function load() {
		requested = true;
		loading = true;
		failed = false;
		try {
			status = await getOpenRouterCredentialStatus();
		} catch {
			status = null;
			failed = true;
		} finally {
			loading = false;
		}
	}

	// Fetched once, the first time an OpenRouter model is involved.
	$effect(() => {
		if (involved && !requested) void load();
	});

	const linkClass =
		'font-medium text-success-fg underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 rounded';
</script>

{#if !involved}
	<!-- every model runs on a local endpoint: no OpenRouter key needed -->
{:else if resolved === 'loading'}
	<div class={cn('flex items-center gap-2 px-1 text-xs text-muted-foreground', className)} role="status">
		<LoaderCircle class="size-3.5 shrink-0 animate-spin motion-reduce:animate-none" aria-hidden="true" />
		Checking your OpenRouter key…
	</div>
{:else if resolved === 'missing'}
	<div class={cn('flex items-start gap-2 rounded-md border px-3 py-2.5 text-sm', toneClasses.warning, className)} role="alert">
		<KeyRound class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
		<div class="min-w-0">
			<p class="font-medium">OpenRouter API key required</p>
			<p class="mt-0.5 text-xs text-muted-foreground">
				Runs call models with your own key and this deployment has no shared one.
				<a href={SETTINGS_HREF} class={linkClass}>Add a key in Settings</a>
			</p>
		</div>
	</div>
{:else if resolved === 'invalid'}
	<div class={cn('flex items-start gap-2 rounded-md border px-3 py-2.5 text-sm', toneClasses.danger, className)} role="alert">
		<CircleAlert class="mt-0.5 size-4 shrink-0" aria-hidden="true" />
		<div class="min-w-0">
			<p class="font-medium">Your OpenRouter key was rejected</p>
			<p class="mt-0.5 text-xs text-muted-foreground">
				{status?.last_validation_message || 'The last validation failed, so runs will fail to start.'}
				<a href={SETTINGS_HREF} class={linkClass}>Update it in Settings</a>
			</p>
		</div>
	</div>
{:else if resolved === 'unknown'}
	<div class={cn('flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-xs', toneClasses.neutral, className)} role="status">
		<Info class="size-3.5 shrink-0" aria-hidden="true" />
		<span class="min-w-0 flex-1">
			Couldn’t check your OpenRouter key. Runs still verify it when they start.
		</span>
		<Button variant="ghost" size="sm" class="h-7 px-2" onclick={load}>Retry</Button>
	</div>
{:else if !hideWhenOk && resolved === 'fallback'}
	<div class={cn('flex items-start gap-2 px-1 text-xs text-muted-foreground', className)} role="status">
		<Info class="mt-px size-3.5 shrink-0 text-info-fg" aria-hidden="true" />
		<span class="min-w-0 flex-1">
			Using the deployment’s shared OpenRouter key.
			<a href={SETTINGS_HREF} class={linkClass}>Add your own</a>
		</span>
	</div>
{:else if !hideWhenOk && resolved === 'ok'}
	<div class={cn('flex items-start gap-2 px-1 text-xs text-muted-foreground', className)} role="status">
		<CircleCheck class="mt-px size-3.5 shrink-0 text-success-fg" aria-hidden="true" />
		<span class="min-w-0 flex-1">
			Using your OpenRouter key
			{#if status?.key_prefix}<code class="mono-tag ml-0.5">{status.key_prefix}…</code>{/if}
			{#if status?.last_validation_status === 'unvalidated'}
				<span>(not validated yet)</span>
			{/if}
		</span>
	</div>
{/if}
