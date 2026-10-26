<script lang="ts">
	/**
	 * Privacy — what this LLM Lab instance actually keeps about you, where, and
	 * what leaves it. Kept in step with Settings → Privacy & data
	 * (PrivacyDataSection), CookieBanner, the preferences store and the drafts
	 * kept in sessionStorage.
	 */
	import { modeStorageKey } from 'mode-watcher';
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import PageHeader from '$lib/components/PageHeader.svelte';
	import Sliders from '@lucide/svelte/icons/sliders-horizontal';

	const browser = $derived([
		{ name: 'sessionid', where: 'Cookie', purpose: 'Keeps you signed in. "Stay signed in" keeps it for 30 days; otherwise it ends when the browser closes.' },
		{ name: 'csrftoken', where: 'Cookie', purpose: 'Protects forms and API calls against cross-site requests.' },
		{ name: 'cookie_consent', where: 'Cookie', purpose: 'Remembers that you accepted the cookie notice (one year).' },
		{ name: 'llm-lab-preferences', where: 'Local storage', purpose: 'Sidebar state, table density and avatar color.' },
		{ name: modeStorageKey.current, where: 'Local storage', purpose: 'Light, dark or system theme.' },
		{ name: 'llm_lab_stay_logged_in', where: 'Local storage', purpose: 'Your last "Stay signed in" choice on the sign-in form.' },
		{ name: 'sample-generator:draft:…, experiment-composer:…', where: 'Session storage', purpose: 'Unsaved generator and experiment drafts; gone when the tab closes.' },
	]);

	const server = [
		{ title: 'Your account', desc: 'Email address, name and profile details. Passwords are stored only as salted hashes.' },
		{ title: 'Keys and secrets', desc: 'OpenRouter, Hugging Face and provider API keys, your GitHub token and notification-channel secrets, encrypted at rest. Personal API tokens are stored only as a hash.' },
		{ title: 'Your work', desc: 'Generated applications, analyses and their findings, experiments and reports you create. Staff of this instance can see every job and run.' },
	];

	const outbound = [
		{ title: 'LLM providers', desc: 'Prompts, requirements and (for AI review) generated code go to the provider behind the endpoint you pick — OpenRouter or a provider you connected — with your key.' },
		{ title: 'GitHub', desc: 'Only when you export an application or results to a repository or Gist.' },
		{ title: 'Notification channels', desc: 'Run summaries go to the Slack, Discord, Teams, ntfy, Telegram or webhook targets you configure.' },
	];

	const rowClass = 'flex items-start justify-between gap-4 px-4 py-3';
</script>

<div class="mx-auto max-w-3xl space-y-4">
	<PageHeader
		title="Privacy & data"
		description="What LLM Lab keeps about you, where it lives, and what leaves this instance. No analytics, advertising or third-party tracking cookies are used."
	>
		<Button size="sm" variant="outline" href="/users/settings#privacy" class="gap-1.5">
			<Sliders class="h-3.5 w-3.5" />
			Privacy settings
		</Button>
	</PageHeader>

	<Card.Root class="gap-0 py-0">
		<Card.Header class="border-b border-border px-4 py-3">
			<Card.Title class="text-sm">Stored in your browser</Card.Title>
			<Card.Description>Essential cookies and local conveniences only. In production the cookie names carry a <span class="font-mono">__Secure-</span> prefix.</Card.Description>
		</Card.Header>
		<ul class="divide-y divide-border">
			{#each browser as item (item.name)}
				<li class={rowClass}>
					<div class="min-w-0 space-y-0.5">
						<p class="break-all font-mono text-[13px]">{item.name}</p>
						<p class="text-sm text-muted-foreground">{item.purpose}</p>
					</div>
					<span class="shrink-0 pt-0.5 text-xs text-muted-foreground">{item.where}</span>
				</li>
			{/each}
		</ul>
	</Card.Root>

	<Card.Root class="gap-0 py-0">
		<Card.Header class="border-b border-border px-4 py-3">
			<Card.Title class="text-sm">Stored on the server</Card.Title>
			<Card.Description>In this instance's database, run by whoever operates it.</Card.Description>
		</Card.Header>
		<ul class="divide-y divide-border">
			{#each server as item (item.title)}
				<li class="px-4 py-3">
					<p class="text-sm font-medium">{item.title}</p>
					<p class="text-sm text-muted-foreground">{item.desc}</p>
				</li>
			{/each}
		</ul>
	</Card.Root>

	<Card.Root class="gap-0 py-0">
		<Card.Header class="border-b border-border px-4 py-3">
			<Card.Title class="text-sm">What leaves this instance</Card.Title>
			<Card.Description>Only as a direct result of something you run or configure.</Card.Description>
		</Card.Header>
		<ul class="divide-y divide-border">
			{#each outbound as item (item.title)}
				<li class="px-4 py-3">
					<p class="text-sm font-medium">{item.title}</p>
					<p class="text-sm text-muted-foreground">{item.desc}</p>
				</li>
			{/each}
		</ul>
	</Card.Root>

	<Card.Root class="gap-0 py-0">
		<Card.Header class="border-b border-border px-4 py-3">
			<Card.Title class="text-sm">Managing your data</Card.Title>
		</Card.Header>
		<ul class="divide-y divide-border text-sm">
			<li class="px-4 py-3">
				<a href="/users/settings#privacy" class="text-primary hover:underline">Settings → Privacy &amp; data</a>
				<span class="text-muted-foreground"> — export, import or reset the preferences kept in this browser.</span>
			</li>
			<li class="px-4 py-3">
				<a href="/users/settings#credentials" class="text-primary hover:underline">Settings → API keys</a>
				<span class="text-muted-foreground"> and </span>
				<a href="/users/settings#tokens" class="text-primary hover:underline">Personal tokens</a>
				<span class="text-muted-foreground"> — remove keys and revoke tokens at any time.</span>
			</li>
			<li class="px-4 py-3 text-muted-foreground">
				Deleting your account or exporting everything stored on the server is not self-service: ask the administrator of this instance.
			</li>
		</ul>
	</Card.Root>
</div>
