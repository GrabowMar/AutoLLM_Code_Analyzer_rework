<script lang="ts">
	/**
	 * ExportMenu — downloads of a launched study: tidy tables (zip), the
	 * replication package (needs a ready report), findings / runs CSV, and
	 * the protocol / report as Markdown. Downloads go through the API so a
	 * refusal (409 "no report yet") shows as a message instead of a file.
	 *
	 * Props: `study`, `reportReady`, `size` of the trigger button.
	 */
	import { DropdownMenu } from 'bits-ui';
	import { toast } from 'svelte-sonner';
	import {
		downloadStudyExport,
		downloadStudyProtocolMarkdown,
		downloadStudyReportMarkdown,
		type Study,
		type StudyExportName
	} from '$lib/api/analysis';
	import { formatApiError } from '$lib/api/core';
	import { cn } from '$lib/utils';
	import Download from '@lucide/svelte/icons/download';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import FileArchive from '@lucide/svelte/icons/file-archive';
	import FileSpreadsheet from '@lucide/svelte/icons/file-spreadsheet';
	import FileText from '@lucide/svelte/icons/file-text';
	import Package from '@lucide/svelte/icons/package';
	import LoaderCircle from '@lucide/svelte/icons/loader-circle';

	interface Props {
		study: Study;
		reportReady: boolean;
	}

	let { study, reportReady }: Props = $props();

	let busy = $state('');
	const launched = $derived(study.status !== 'draft' && study.status !== 'locked');
	const hasProtocol = $derived(!!study.protocol_hash);

	async function run(key: string, action: () => Promise<string>) {
		busy = key;
		try {
			const name = await action();
			toast.success(`Downloaded ${name}`);
		} catch (e) {
			toast.error(formatApiError(e, 'The download failed.'));
		} finally {
			busy = '';
		}
	}

	function exportItem(name: StudyExportName) {
		void run(name, () => downloadStudyExport(study.id, name));
	}

	const itemClass =
		'flex cursor-pointer select-none items-start gap-2 rounded-sm px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50';
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class="inline-flex h-8 cursor-pointer items-center gap-2 rounded border border-border bg-transparent px-3 text-xs font-medium transition-colors hover:border-primary/50 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50"
		disabled={!hasProtocol}
	>
		{#if busy}<LoaderCircle
				class="h-4 w-4 animate-spin motion-reduce:animate-none"
				aria-hidden="true"
			/>{:else}<Download class="h-4 w-4" aria-hidden="true" />{/if}
		Downloads
		<ChevronDown class="h-3.5 w-3.5 opacity-70" aria-hidden="true" />
	</DropdownMenu.Trigger>
	<DropdownMenu.Portal>
		<DropdownMenu.Content
			align="end"
			sideOffset={6}
			class="z-50 w-72 rounded-md border border-border bg-popover p-1 text-popover-foreground shadow-md"
		>
			<DropdownMenu.Group>
				<DropdownMenu.GroupHeading
					class="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
				>
					Documents
				</DropdownMenu.GroupHeading>
				<DropdownMenu.Item
					class={itemClass}
					disabled={!hasProtocol}
					onSelect={() => run('protocol', () => downloadStudyProtocolMarkdown(study.id))}
				>
					<FileText class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span
						><span class="block">protocol.md</span><span
							class="block text-[11px] text-muted-foreground">Frozen pre-run protocol</span
						></span
					>
				</DropdownMenu.Item>
				<DropdownMenu.Item
					class={itemClass}
					disabled={!reportReady}
					onSelect={() => run('report', () => downloadStudyReportMarkdown(study.id))}
				>
					<FileText class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span>
						<span class="block">report.md</span>
						<span class="block text-[11px] text-muted-foreground"
							>{reportReady ? 'Thesis-ready tables' : 'Generate the report first'}</span
						>
					</span>
				</DropdownMenu.Item>
			</DropdownMenu.Group>
			<DropdownMenu.Separator class="my-1 h-px bg-border" />
			<DropdownMenu.Group>
				<DropdownMenu.GroupHeading
					class="px-2 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
				>
					Data
				</DropdownMenu.GroupHeading>
				<DropdownMenu.Item
					class={itemClass}
					disabled={!launched}
					onSelect={() => exportItem('tables.zip')}
				>
					<FileArchive class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span
						><span class="block">tables.zip</span><span
							class="block text-[11px] text-muted-foreground">Tidy CSV tables</span
						></span
					>
				</DropdownMenu.Item>
				<DropdownMenu.Item
					class={itemClass}
					disabled={!launched || !reportReady}
					onSelect={() => exportItem('replication.zip')}
				>
					<Package class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span>
						<span class="block">replication.zip</span>
						<span class="block text-[11px] text-muted-foreground">
							{reportReady
								? 'Tables + protocol + report + plan + SHA256SUMS'
								: 'Needs a ready report'}
						</span>
					</span>
				</DropdownMenu.Item>
				<DropdownMenu.Item
					class={itemClass}
					disabled={!launched}
					onSelect={() => exportItem('findings.csv')}
				>
					<FileSpreadsheet class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span
						><span class="block">findings.csv</span><span
							class="block text-[11px] text-muted-foreground">Every finding of every run</span
						></span
					>
				</DropdownMenu.Item>
				<DropdownMenu.Item
					class={cn(itemClass)}
					disabled={!launched}
					onSelect={() => exportItem('runs.csv')}
				>
					<FileSpreadsheet class="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
					<span
						><span class="block">runs.csv</span><span
							class="block text-[11px] text-muted-foreground">One row per run with outcomes</span
						></span
					>
				</DropdownMenu.Item>
			</DropdownMenu.Group>
		</DropdownMenu.Content>
	</DropdownMenu.Portal>
</DropdownMenu.Root>
