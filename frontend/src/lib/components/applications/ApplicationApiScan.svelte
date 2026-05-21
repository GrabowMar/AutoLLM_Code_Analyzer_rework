<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import Shield from '@lucide/svelte/icons/shield';
import Database from '@lucide/svelte/icons/database';
import { httpColors } from './utils';

interface Endpoint { method: string; path: string }
interface Model { name: string; fields?: string[] }
interface BackendScan {
	endpoints?: Endpoint[];
	models?: Model[];
	has_auth?: boolean;
	has_admin?: boolean;
}

interface Props {
	scan: BackendScan;
}

let { scan }: Props = $props();
</script>

<section id="scan" class="space-y-4">
	<h2 class="text-lg font-semibold flex items-center gap-2"><Shield class="h-5 w-5" /> API Scan</h2>
	<div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
		<!-- Endpoints -->
		<Card.Root>
			<Card.Header class="pb-3">
				<Card.Title class="text-sm font-medium flex items-center gap-2">
					API Endpoints
					<Badge variant="outline" class="text-xs">{scan.endpoints?.length ?? 0}</Badge>
				</Card.Title>
			</Card.Header>
			<Card.Content>
				{#if scan.endpoints && scan.endpoints.length > 0}
					<div class="space-y-1.5">
						{#each scan.endpoints as ep}
							<div class="flex items-center gap-2 text-sm font-mono">
								<span class="text-xs font-bold w-12 {httpColors[ep.method] ?? 'text-zinc-400'}">{ep.method}</span>
								<span class="text-foreground/90">{ep.path}</span>
							</div>
						{/each}
					</div>
				{:else}
					<p class="text-sm text-muted-foreground">No endpoints detected</p>
				{/if}
			</Card.Content>
		</Card.Root>

		<!-- Models -->
		<Card.Root>
			<Card.Header class="pb-3">
				<Card.Title class="text-sm font-medium flex items-center gap-2">
					Data Models
					<Badge variant="outline" class="text-xs">{scan.models?.length ?? 0}</Badge>
				</Card.Title>
			</Card.Header>
			<Card.Content>
				{#if scan.models && scan.models.length > 0}
					<div class="space-y-3">
						{#each scan.models as model}
							<div>
								<div class="font-medium text-sm flex items-center gap-2">
									<Database class="h-3.5 w-3.5 text-muted-foreground" />
									{model.name}
									<Badge variant="outline" class="text-[10px]">{model.fields?.length ?? 0} fields</Badge>
								</div>
								{#if model.fields && model.fields.length > 0}
									<div class="flex flex-wrap gap-1 mt-1 ml-5">
										{#each model.fields as field}
											<Badge variant="outline" class="text-[10px] font-mono">{field}</Badge>
										{/each}
									</div>
								{/if}
							</div>
						{/each}
					</div>
				{:else}
					<p class="text-sm text-muted-foreground">No models detected</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Detection badges -->
	<div class="flex items-center gap-3">
		<Badge variant="outline" class="text-xs {scan.has_auth ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' : 'bg-zinc-500/15 text-zinc-400'}">
			<Shield class="h-3 w-3 mr-1" />
			Auth: {scan.has_auth ? 'Detected' : 'None'}
		</Badge>
		<Badge variant="outline" class="text-xs {scan.has_admin ? 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30' : 'bg-zinc-500/15 text-zinc-400'}">
			<Shield class="h-3 w-3 mr-1" />
			Admin: {scan.has_admin ? 'Detected' : 'None'}
		</Badge>
	</div>
</section>
