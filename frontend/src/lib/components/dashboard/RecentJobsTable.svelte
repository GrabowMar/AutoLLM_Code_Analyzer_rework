<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import AppWindow from '@lucide/svelte/icons/app-window';
	import ArrowRight from '@lucide/svelte/icons/arrow-right';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import type { RecentApp } from '$lib/utils/dashboard';
	import { rowLink, shortModelName, modelNamePrefix } from '$lib/components/table';

	interface Props {
		jobs: RecentApp[];
	}

	let { jobs }: Props = $props();
</script>

<Card.Root class="gap-0 overflow-clip py-0">
	<Card.Header class="border-b border-border py-3">
		<div class="flex items-center justify-between">
			<div class="flex items-center gap-2">
				<AppWindow class="h-4 w-4 text-muted-foreground" />
				<div>
					<Card.Title>Recent Generation Jobs</Card.Title>
					<Card.Description>Latest application generation runs.</Card.Description>
				</div>
			</div>
			<Button variant="ghost" size="sm" href="/applications" class="text-xs">
				View all
				<ArrowRight class="ml-1 h-3 w-3" />
			</Button>
		</div>
	</Card.Header>
	<Card.Content class="p-0">
		<div class="overflow-x-auto">
			<table class="table-card-mobile">
				<thead>
					<tr>
						<th>Job</th>
						<th class="hide-mobile">Model</th>
						<th>Status</th>
						<th>Created</th>
					</tr>
				</thead>
				<tbody>
					{#each jobs as app (app.id)}
						{@const href = `/applications/${app.id}`}
						<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (the name link is the keyboard path) -->
						<tr class="row-link" onclick={rowLink(href)}>
							<td data-label="Job">
								<a {href} class="block max-w-[26rem] truncate font-medium transition-colors hover:text-primary" title={app.name}>{app.name}</a>
							</td>
							<td class="hide-mobile" data-label="Model">
								{#if app.model}
									{@const provider = modelNamePrefix(app.model)}
									<span class="block max-w-[18rem] truncate" title={app.model}>{shortModelName(app.model)}{#if provider}<span class="text-muted-foreground">{` · ${provider}`}</span>{/if}</span>
								{:else}
									<span class="text-muted-foreground">—</span>
								{/if}
							</td>
							<td class="whitespace-nowrap" data-label="Status">
								<StatusBadge status={app.status} />
							</td>
							<td class="whitespace-nowrap" data-label="Created">
								<span class="text-xs text-muted-foreground">{app.time}</span>
							</td>
						</tr>
					{:else}
						<tr>
							<td colspan="4">
								<EmptyState compact icon={AppWindow} title="No generation jobs yet">
									{#snippet action()}
										<Button href="/sample-generator" size="sm" variant="outline">Generate an app</Button>
									{/snippet}
								</EmptyState>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	</Card.Content>
</Card.Root>
