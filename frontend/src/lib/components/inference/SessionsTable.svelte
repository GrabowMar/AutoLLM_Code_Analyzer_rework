<script lang="ts">
	/**
	 * SessionsTable — relay sessions of an endpoint (owner/staff only data).
	 *
	 * Props:
	 * - `sessions`: `SessionOut[]`, newest first. Ended sessions are dimmed.
	 * - `now`: clock for "last seen" (pass a ticking value to keep it fresh).
	 * - `newHosts`: hostnames / IPs not seen before for the same token; flagged
	 *   so a stolen token stands out.
	 */
	import { cn } from '$lib/utils';
	import type { SessionOut } from '$lib/api/types/inference';
	import { formatDateTime } from '$lib/utils/formatters';
	import StatusBadge from '$lib/components/StatusBadge.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import { formatAgo } from './endpoint-display';
	import Unplug from '@lucide/svelte/icons/unplug';

	interface Props {
		sessions: SessionOut[];
		now?: number;
		newHosts?: ReadonlySet<string>;
	}

	let { sessions, now = Date.now(), newHosts = new Set() }: Props = $props();

	function roleStatus(s: SessionOut): string {
		if (s.ended_at) return 'ended';
		return s.role === 'active' ? 'active' : 'standby';
	}
</script>

{#if sessions.length === 0}
	<EmptyState
		compact
		icon={Unplug}
		title="No relay sessions yet"
		description="Start the relay on the model machine; it shows up here when it connects."
	/>
{:else}
	<div class="relative overflow-x-auto md:rounded-md md:border md:border-border">
		<table class="table-card-mobile w-full text-sm md:[&_td]:px-2 md:[&_th]:px-2">
			<thead>
				<tr>
					<th scope="col">Role</th>
					<th scope="col">Host</th>
					<th scope="col">Relay</th>
					<th scope="col">IP</th>
					<th scope="col" class="text-right">Capacity</th>
					<th scope="col" class="text-right">In flight</th>
					<th scope="col">Connected</th>
					<th scope="col">Last seen</th>
				</tr>
			</thead>
			<tbody>
				{#each sessions as s (s.id)}
					{@const flagged = !s.ended_at && (newHosts.has(s.hostname) || (!!s.client_ip && newHosts.has(s.client_ip)))}
					<tr class={cn(s.ended_at && 'text-muted-foreground')}>
						<td data-label="Role">
							<StatusBadge status={roleStatus(s)} tones={{ active: 'success', standby: 'neutral', ended: 'neutral' }} />
						</td>
						<td data-label="Host" class="md:max-w-48">
							<span class="block min-w-0 break-all font-mono text-xs md:truncate" title={s.hostname}>{s.hostname || '—'}</span>
							{#if flagged}
								<span class="mt-0.5 block text-[11px] text-warning-fg" title="This token has not connected from here before">new host for this token</span>
							{/if}
						</td>
						<td data-label="Relay" class="font-mono text-xs">
							v{s.relay_version}
							<span class="block text-[11px] text-muted-foreground" title="Token prefix">{s.token_prefix}…</span>
						</td>
						<td data-label="IP" class="font-mono text-xs">{s.client_ip ?? '—'}</td>
						<td data-label="Capacity" class="text-right tabular-nums">{s.capacity}</td>
						<td data-label="In flight" class="text-right tabular-nums">{s.ended_at ? '—' : s.inflight}</td>
						<td data-label="Connected" class="text-xs" title={formatDateTime(s.started_at)}>{formatAgo(s.started_at, now)}</td>
						<td data-label="Last seen" class="text-xs" title={formatDateTime(s.last_seen_at)}>
							{#if s.ended_at}
								ended {formatAgo(s.ended_at, now)}{#if s.ended_reason}<span class="block text-[11px]">{s.ended_reason.replace(/_/g, ' ')}</span>{/if}
							{:else}
								{formatAgo(s.last_seen_at, now)}
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}
