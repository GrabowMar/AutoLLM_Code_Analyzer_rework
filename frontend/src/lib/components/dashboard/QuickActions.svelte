<script lang="ts">
	import * as Card from '$lib/components/ui/card';
	import { Button } from '$lib/components/ui/button';
	import Cpu from '@lucide/svelte/icons/cpu';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import CloudDownload from '@lucide/svelte/icons/cloud-download';
	import Eraser from '@lucide/svelte/icons/eraser';
	import Activity from '@lucide/svelte/icons/activity';
	import Loader2 from '@lucide/svelte/icons/loader-2';

	interface Props {
		isRefreshing: boolean;
		pendingAction: string | null;
		onRefresh: () => void;
		onSyncModels: () => void;
		onClearCaches: () => void;
		onClearStuck: () => void;
	}

	let { isRefreshing, pendingAction, onRefresh, onSyncModels, onClearCaches, onClearStuck }: Props =
		$props();
</script>

<Card.Root>
	<Card.Header>
		<div class="flex items-center gap-2">
			<Cpu class="h-4 w-4 text-muted-foreground" />
			<div>
				<Card.Title>Quick Actions</Card.Title>
				<Card.Description>Maintenance shortcuts</Card.Description>
			</div>
		</div>
	</Card.Header>
	<Card.Content>
		<div class="grid grid-cols-2 gap-2">
			<Button
				variant="outline"
				size="sm"
				class="justify-start"
				onclick={onRefresh}
				disabled={isRefreshing}
			>
				{#if isRefreshing}<Loader2
						class="mr-2 h-3.5 w-3.5 animate-spin"
					/>{:else}<RefreshCw class="mr-2 h-3.5 w-3.5 text-[color:var(--accent-2)]" />{/if}
				Refresh
			</Button>
			<Button
				variant="outline"
				size="sm"
				class="hover-glow justify-start"
				onclick={onSyncModels}
				disabled={pendingAction !== null}
			>
				{#if pendingAction === 'sync'}<Loader2
						class="mr-2 h-3.5 w-3.5 animate-spin"
					/>{:else}<CloudDownload class="mr-2 h-3.5 w-3.5 text-primary" />{/if}
				Sync Models
			</Button>
			<Button
				variant="outline"
				size="sm"
				class="justify-start"
				onclick={onClearCaches}
				disabled={pendingAction !== null}
			>
				{#if pendingAction === 'caches'}<Loader2
						class="mr-2 h-3.5 w-3.5 animate-spin"
					/>{:else}<Eraser class="mr-2 h-3.5 w-3.5 text-amber-500" />{/if}
				Clear Caches
			</Button>
			<Button
				variant="outline"
				size="sm"
				class="justify-start"
				onclick={onClearStuck}
				disabled={pendingAction !== null}
			>
				{#if pendingAction === 'stuck'}<Loader2
						class="mr-2 h-3.5 w-3.5 animate-spin"
					/>{:else}<Activity class="mr-2 h-3.5 w-3.5 text-emerald-500" />{/if}
				Clear Stuck
			</Button>
			<Button variant="outline" size="sm" class="col-span-2 justify-start" href="/system">
				<Cpu class="mr-2 h-3.5 w-3.5 text-muted-foreground" />
				Open System Panel
			</Button>
		</div>
	</Card.Content>
</Card.Root>
