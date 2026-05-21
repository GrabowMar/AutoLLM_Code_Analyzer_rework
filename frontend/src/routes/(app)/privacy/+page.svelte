<script lang="ts">
	import ShieldHalf from '@lucide/svelte/icons/shield-half';
	import CheckCircle from '@lucide/svelte/icons/check-circle';
	import Sliders from '@lucide/svelte/icons/sliders';
	import Download from '@lucide/svelte/icons/download';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Lock from '@lucide/svelte/icons/lock';
	import Server from '@lucide/svelte/icons/server';
	import ShieldCheck from '@lucide/svelte/icons/shield-check';
	import UserCog from '@lucide/svelte/icons/user-cog';
	import Mail from '@lucide/svelte/icons/mail';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
</script>

<svelte:head>
	<title>Privacy Policy & Data Usage - LLM Lab</title>
</svelte:head>

<div class="max-w-5xl mx-auto space-y-6">
	<!-- Header -->
	<div class="page-header">
		<div class="flex items-center gap-4">
			<div class="flex size-12 items-center justify-center rounded-md bg-primary/10 text-primary shrink-0 border border-primary/20">
				<ShieldHalf class="size-6" />
			</div>
			<div>
				<h1>Privacy Policy &amp; Data Usage</h1>
				<p>Last updated: January 2026</p>
			</div>
		</div>
	</div>
	<div class="rounded-md border border-primary/30 bg-primary/[0.05] px-4 py-3 text-sm text-foreground">
		<strong class="text-primary">Your Privacy Matters:</strong> This document explains what data we collect, how we use it, and how you can control your privacy settings.
	</div>

	<!-- Quick Summary -->
	<Card.Root class="border-primary">
		<Card.Header>
			<Card.Title class="text-primary flex items-center gap-2">
				<CheckCircle class="size-5" />
				Quick Summary
			</Card.Title>
		</Card.Header>
		<Card.Content>
			<div class="grid gap-4 sm:grid-cols-2">
				{#each [
					{ title: 'Data Stored Locally', desc: 'Most data is stored in your browser using localStorage' },
					{ title: 'No Third-Party Tracking', desc: "We don't share your data with advertisers" },
					{ title: 'You Control Your Data', desc: 'Manage cookie preferences at any time' },
					{ title: 'Essential Only Option', desc: 'Disable all optional tracking and analytics' }
				] as item}
					<div class="flex items-start gap-3">
						<CheckCircle class="size-4 text-green-500 mt-0.5 shrink-0" />
						<div>
							<strong class="text-sm">{item.title}:</strong>
							<span class="text-sm text-muted-foreground ml-1">{item.desc}</span>
						</div>
					</div>
				{/each}
			</div>
		</Card.Content>
	</Card.Root>

	<!-- Data Categories -->
	<div class="grid gap-4 sm:grid-cols-2">
		<!-- Essential Data -->
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2">
					<Badge class="bg-green-500 text-white">Required</Badge>
					Essential Data
				</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				<p class="text-sm text-muted-foreground">Required for core functionality. Cannot be disabled.</p>
				<div>
					<h4 class="text-sm font-semibold mb-2">What We Store:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li><strong>Authentication Tokens:</strong> Session management and login state</li>
						<li><strong>CSRF Tokens:</strong> Security protection against cross-site attacks</li>
						<li><strong>Theme Preference:</strong> Dark/light mode selection</li>
						<li><strong>Language Settings:</strong> UI language preference</li>
						<li><strong>Security State:</strong> Login attempts and session validation</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Storage Method:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li>HTTP-only cookies (secure, not accessible via JavaScript)</li>
						<li>localStorage for theme preferences</li>
						<li>Session storage for temporary data</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Retention Period:</h4>
					<p class="text-sm text-muted-foreground">Session data expires after 24 hours of inactivity. Theme preferences persist indefinitely.</p>
				</div>
			</Card.Content>
		</Card.Root>

		<!-- Analytics Data -->
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2">
					<Badge class="bg-blue-500 text-white">Optional</Badge>
					Analytics & Performance
				</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				<p class="text-sm text-muted-foreground">Helps us improve the application.</p>
				<div>
					<h4 class="text-sm font-semibold mb-2">What We Collect:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li><strong>Page Views:</strong> Which pages you visit and navigation patterns</li>
						<li><strong>Feature Usage:</strong> Which features you use most often</li>
						<li><strong>Performance Metrics:</strong> Page load times, API response times</li>
						<li><strong>Error Logs:</strong> JavaScript errors and failed requests</li>
						<li><strong>Session Duration:</strong> How long you use the application</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">How We Use It:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li>Identify performance bottlenecks</li>
						<li>Understand which features are most valuable</li>
						<li>Detect and fix bugs more quickly</li>
						<li>Improve user experience</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Third-Party Services:</h4>
					<p class="text-sm text-muted-foreground">None. All analytics are processed internally and never shared.</p>
				</div>
			</Card.Content>
		</Card.Root>

		<!-- Functional Data -->
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2">
					<Badge class="bg-purple-500 text-white">Optional</Badge>
					Functional & Preferences
				</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				<p class="text-sm text-muted-foreground">Personalizes your experience.</p>
				<div>
					<h4 class="text-sm font-semibold mb-2">What We Store:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li><strong>UI Preferences:</strong> Sidebar collapsed/expanded state</li>
						<li><strong>Table Settings:</strong> Column visibility, sort order, page size</li>
						<li><strong>Filter Preferences:</strong> Saved filter configurations</li>
						<li><strong>Dashboard Layout:</strong> Widget positions and visibility</li>
						<li><strong>Recent Searches:</strong> Quick access to previous queries</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Benefits:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li>Remember your UI customizations</li>
						<li>Faster access to frequently used settings</li>
						<li>Consistent experience across sessions</li>
						<li>Reduce repetitive configuration tasks</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Storage Location:</h4>
					<p class="text-sm text-muted-foreground">localStorage in your browser. Never sent to the server.</p>
				</div>
			</Card.Content>
		</Card.Root>

		<!-- AI Data -->
		<Card.Root>
			<Card.Header>
				<Card.Title class="flex items-center gap-2">
					<Badge class="bg-indigo-500 text-white">Optional</Badge>
					AI Processing & History
				</Card.Title>
			</Card.Header>
			<Card.Content class="space-y-4">
				<p class="text-sm text-muted-foreground">Improves AI analysis performance.</p>
				<div>
					<h4 class="text-sm font-semibold mb-2">What We Cache:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li><strong>Analysis Results:</strong> Previous AI analysis outputs</li>
						<li><strong>Model Configurations:</strong> Your preferred AI model settings</li>
						<li><strong>Prompt History:</strong> Recently used prompts and templates</li>
						<li><strong>Result Previews:</strong> Quick access to past results</li>
						<li><strong>Model Ratings:</strong> Your feedback on model performance</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Why We Cache:</h4>
					<ul class="text-sm text-muted-foreground space-y-1 list-disc list-inside">
						<li>Avoid re-running expensive AI analyses</li>
						<li>Faster loading of previous results</li>
						<li>Quick access to analysis history</li>
						<li>Better recommendations based on usage</li>
					</ul>
				</div>
				<div>
					<h4 class="text-sm font-semibold mb-2">Data Handling:</h4>
					<p class="text-sm text-muted-foreground">Cached data is stored both locally (for speed) and on our servers (for persistence). All data is encrypted in transit and at rest.</p>
				</div>
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Your Rights -->
	<Card.Root>
		<Card.Header>
			<Card.Title class="flex items-center gap-2">
				<UserCog class="size-5 text-primary" />
				Your Rights & Controls
			</Card.Title>
		</Card.Header>
		<Card.Content>
			<div class="grid gap-6 sm:grid-cols-3">
				<div class="flex items-start gap-3">
					<Sliders class="size-5 text-primary mt-0.5 shrink-0" />
					<div>
						<h4 class="font-medium text-sm mb-1">Manage Preferences</h4>
						<p class="text-sm text-muted-foreground mb-3">Control which categories of data you allow.</p>
						<Button size="sm" href="/users/settings">Cookie Settings</Button>
					</div>
				</div>
				<div class="flex items-start gap-3">
					<Download class="size-5 text-primary mt-0.5 shrink-0" />
					<div>
						<h4 class="font-medium text-sm mb-1">Export Your Data</h4>
						<p class="text-sm text-muted-foreground mb-3">Download all data we've collected about you.</p>
						<Button size="sm" variant="outline" href="/users/settings">Request Export</Button>
					</div>
				</div>
				<div class="flex items-start gap-3">
					<Trash2 class="size-5 text-primary mt-0.5 shrink-0" />
					<div>
						<h4 class="font-medium text-sm mb-1">Delete Your Data</h4>
						<p class="text-sm text-muted-foreground mb-3">Request complete deletion of your account and data.</p>
						<Button size="sm" variant="destructive" href="/users/settings">Delete Account</Button>
					</div>
				</div>
			</div>
		</Card.Content>
	</Card.Root>

	<!-- Security -->
	<Card.Root>
		<Card.Header>
			<Card.Title class="flex items-center gap-2">
				<Lock class="size-5 text-green-600" />
				Security & Protection
			</Card.Title>
		</Card.Header>
		<Card.Content>
			<div class="grid gap-4 sm:grid-cols-2">
				<div>
					<h4 class="text-sm font-semibold flex items-center gap-2 mb-1">
						<Lock class="size-4 text-muted-foreground" /> Encryption
					</h4>
					<p class="text-sm text-muted-foreground">All data transmitted between your browser and our servers is encrypted using industry-standard TLS/SSL protocols.</p>
				</div>
				<div>
					<h4 class="text-sm font-semibold flex items-center gap-2 mb-1">
						<Server class="size-4 text-muted-foreground" /> Secure Storage
					</h4>
					<p class="text-sm text-muted-foreground">Server-side data is encrypted at rest and stored in secure, access-controlled databases.</p>
				</div>
				<div>
					<h4 class="text-sm font-semibold flex items-center gap-2 mb-1">
						<ShieldCheck class="size-4 text-muted-foreground" /> Access Controls
					</h4>
					<p class="text-sm text-muted-foreground">Strict authentication and authorization prevent unauthorized access to your data.</p>
				</div>
				<div>
					<h4 class="text-sm font-semibold flex items-center gap-2 mb-1">
						<UserCog class="size-4 text-muted-foreground" /> Privacy by Design
					</h4>
					<p class="text-sm text-muted-foreground">We collect only the minimum data necessary and delete it when no longer needed.</p>
				</div>
			</div>
		</Card.Content>
	</Card.Root>

	<!-- Contact -->
	<Card.Root>
		<Card.Content class="pt-6">
			<h3 class="font-semibold text-lg flex items-center gap-2 mb-3">
				<Mail class="size-5" />
				Questions or Concerns?
			</h3>
			<p class="text-sm text-muted-foreground mb-4">
				If you have questions about this privacy policy, how we handle your data, or wish to exercise your rights, please contact us.
			</p>
			<div class="flex flex-wrap gap-3">
				<Button href="mailto:privacy@thesisplatform.com">
					Contact Privacy Team
				</Button>
				<Button variant="outline" href="/users/settings">
					Manage Cookie Settings
				</Button>
			</div>
		</Card.Content>
	</Card.Root>
</div>
