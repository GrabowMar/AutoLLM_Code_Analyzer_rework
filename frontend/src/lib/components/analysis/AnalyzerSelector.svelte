<script lang="ts">
import * as Card from '$lib/components/ui/card';
import { Badge } from '$lib/components/ui/badge';
import { Button } from '$lib/components/ui/button';
import RefreshCw from '@lucide/svelte/icons/refresh-cw';
import Loader from '@lucide/svelte/icons/loader-circle';
import AlertCircle from '@lucide/svelte/icons/alert-circle';
import { analyzerTypeLabels } from '$lib/constants/analysis';
import type { AnalyzerInfo } from '$lib/api/client';

interface Props {
	analyzersLoading: boolean;
	analyzersError: string;
	analyzers: AnalyzerInfo[];
	selectedAnalyzers: Set<string>;
	onToggleAnalyzer: (name: string) => void;
	onSelectAll: () => void;
	onClearAll: () => void;
	onReload: () => void;
}

let {
	analyzersLoading,
	analyzersError,
	analyzers,
	selectedAnalyzers,
	onToggleAnalyzer,
	onSelectAll,
	onClearAll,
	onReload,
}: Props = $props();

const analyzerTypes = ['static', 'dynamic', 'performance', 'ai'] as const;
const typeColors: Record<string, string> = {
	static: 'text-blue-400 border-blue-500/30 bg-blue-500/10',
	dynamic: 'text-emerald-500 border-emerald-500/30 bg-emerald-500/10',
	performance: 'text-cyan-400 border-cyan-500/30 bg-cyan-500/10',
	ai: 'text-amber-500 border-amber-500/30 bg-amber-500/10',
};

function analyzersByType(type: string) {
	return analyzers.filter((a) => a.type === type);
}
</script>

<div class="space-y-4">
	<div class="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
		<div class="flex items-center gap-2">
			<Button variant="outline" size="sm" onclick={onSelectAll}>Select All</Button>
			<Button variant="outline" size="sm" onclick={onClearAll}>Clear All</Button>
			<Button variant="outline" size="sm" onclick={onReload}>
				<RefreshCw class="mr-1.5 h-3 w-3" /> Refresh
			</Button>
		</div>
		<Badge variant="outline">{selectedAnalyzers.size} analyzer{selectedAnalyzers.size !== 1 ? 's' : ''} selected</Badge>
	</div>

	{#if analyzersLoading}
		<div class="flex items-center justify-center py-12 text-muted-foreground">
			<Loader class="mr-2 h-4 w-4 animate-spin" />
			Loading analyzers…
		</div>
	{:else if analyzersError}
		<div class="flex items-center gap-2 rounded-md border border-red-500/30 bg-red-500/10 p-4 text-sm text-red-400">
			<AlertCircle class="h-4 w-4 shrink-0" />
			{analyzersError}
			<Button variant="outline" size="sm" class="ml-auto" onclick={onReload}>Retry</Button>
		</div>
	{:else}
		<div class="grid grid-cols-1 gap-4 md:grid-cols-2">
			{#each analyzerTypes as type}
				{@const group = analyzersByType(type)}
				{#if group.length > 0}
					{@const colorClasses = typeColors[type] ?? ''}
					{@const borderClass = colorClasses.split(' ').filter((c) => c.startsWith('border-')).join(' ')}
					{@const textClass = colorClasses.split(' ').filter((c) => c.startsWith('text-')).join(' ')}
					<Card.Root class="border {borderClass}">
						<Card.Header>
							<div class="flex items-center justify-between">
								<Card.Title class="text-sm {textClass}">{analyzerTypeLabels[type] ?? type}</Card.Title>
								<Badge variant="outline" class="text-[10px]">
									{group.filter((a) => a.available).length}/{group.length} available
								</Badge>
							</div>
							<Card.Description>{group.length} analyzer{group.length !== 1 ? 's' : ''}</Card.Description>
						</Card.Header>
						<Card.Content>
							<div class="space-y-2">
								{#each group as analyzer}
									<label class="flex items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors {analyzer.available ? 'cursor-pointer hover:bg-muted/30' : 'cursor-not-allowed opacity-50'}">
										<input
											type="checkbox"
											class="rounded"
											checked={selectedAnalyzers.has(analyzer.name)}
											disabled={!analyzer.available}
											onchange={() => onToggleAnalyzer(analyzer.name)}
										/>
										<div class="min-w-0 flex-1">
											<div class="text-sm font-medium">{analyzer.display_name}</div>
											<div class="text-xs text-muted-foreground">{analyzer.description}</div>
											{#if !analyzer.available && analyzer.availability_message}
												<div class="mt-0.5 text-xs text-amber-500">{analyzer.availability_message}</div>
											{/if}
										</div>
									</label>
								{/each}
							</div>
						</Card.Content>
					</Card.Root>
				{/if}
			{/each}
		</div>
	{/if}
</div>
