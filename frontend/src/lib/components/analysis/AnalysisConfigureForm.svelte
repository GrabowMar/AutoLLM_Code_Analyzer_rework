<script lang="ts">
import * as Card from '$lib/components/ui/card';
import Settings from '@lucide/svelte/icons/settings';
import type { AnalyzerInfo } from '$lib/api/client';

interface Props {
	taskName: string;
	autoStart: boolean;
	liveTarget: boolean;
	showLiveTargetOption: boolean;
	selectedAnalyzersList: AnalyzerInfo[];
	analyzerSettings: Record<string, string>;
	settingsErrors: Record<string, string>;
}

let {
	taskName = $bindable(),
	autoStart = $bindable(),
	liveTarget = $bindable(),
	showLiveTargetOption,
	selectedAnalyzersList,
	analyzerSettings = $bindable(),
	settingsErrors,
}: Props = $props();
</script>

<div class="space-y-4">
	<Card.Root>
		<Card.Header>
			<div class="flex items-center gap-2">
				<Settings class="h-4 w-4 text-muted-foreground" />
				<Card.Title class="text-sm">Task Configuration</Card.Title>
			</div>
		</Card.Header>
		<Card.Content>
			<div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
				<div>
					<label class="text-sm font-medium">Task Name</label>
					<input
						type="text"
						class="mt-1 h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
						placeholder="Optional task name…"
						bind:value={taskName}
					/>
				</div>
				<div class="flex items-center gap-2 pt-6">
					<input type="checkbox" id="autoStart" bind:checked={autoStart} class="rounded" />
					<label for="autoStart" class="text-sm">Auto-start analysis</label>
				</div>
				{#if showLiveTargetOption}
					<div class="flex items-center gap-2 pt-6">
						<input type="checkbox" id="liveTarget" bind:checked={liveTarget} class="rounded" />
						<label for="liveTarget" class="text-sm">
							Run against live container
							<span class="ml-1 text-xs text-muted-foreground">(starts Docker container for dynamic analysis)</span>
						</label>
					</div>
				{/if}
			</div>
		</Card.Content>
	</Card.Root>

	{#if selectedAnalyzersList.length > 0}
		<Card.Root>
			<Card.Header>
				<Card.Title class="text-sm">Analyzer Settings</Card.Title>
				<Card.Description>Override default configuration per analyzer (JSON).</Card.Description>
			</Card.Header>
			<Card.Content>
				<div class="space-y-4">
					{#each selectedAnalyzersList as analyzer}
						<div>
							<label class="mb-1 block text-sm font-medium">{analyzer.display_name}</label>
							<textarea
								class="h-20 w-full rounded-md border border-input bg-background p-2 font-mono text-xs"
								placeholder={JSON.stringify(analyzer.default_config, null, 2) || '{}'}
								bind:value={analyzerSettings[analyzer.name]}
							></textarea>
							{#if settingsErrors[analyzer.name]}
								<p class="text-xs text-red-500 mt-1">{settingsErrors[analyzer.name]}</p>
							{/if}
						</div>
					{/each}
				</div>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
