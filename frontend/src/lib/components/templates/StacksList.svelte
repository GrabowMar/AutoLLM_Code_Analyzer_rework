<script lang="ts">
	/**
	 * StacksList — stack cards. Every stack can be duplicated; only your own user
	 * stacks can be edited (new version) and archived — another user's approved
	 * stack is listed (you may run it) but the server refuses changes to it. A
	 * user stack awaiting approval runs only for its owner.
	 */
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import LoadingState from '$lib/components/LoadingState.svelte';
	import EmptyState from '$lib/components/EmptyState.svelte';
	import Layers from '@lucide/svelte/icons/layers';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Copy from '@lucide/svelte/icons/copy';
	import Archive from '@lucide/svelte/icons/archive';
	import type { Stack } from '$lib/api/generation';

	interface Props {
		stacks: Stack[];
		loading: boolean;
		compact: boolean;
		activeSlug?: string | null;
		onEdit?: (s: Stack) => void;
		onDuplicate?: (s: Stack) => void;
		onArchive?: (s: Stack) => void;
	}
	let { stacks, loading, compact, activeSlug = null, onEdit, onDuplicate, onArchive }: Props = $props();

	// `is_own` is newer than some API responses: a missing field means "not yours".
	function canChange(stack: Stack): boolean {
		return !stack.is_builtin && (stack as Stack & { is_own?: boolean }).is_own === true;
	}
</script>

{#if loading}
	<LoadingState label="loading stacks" />
{:else if stacks.length === 0}
	<EmptyState icon={Layers} title="No stacks found" description="Clear the search, or run migrations to seed the builtin stacks." />
{:else}
	<div class="grid gap-3 sm:grid-cols-2 {compact ? 'xl:grid-cols-1' : 'xl:grid-cols-3'}">
		{#each stacks as stack (stack.slug)}
			<div
				class="flex flex-col rounded-md border border-border bg-card text-card-foreground {activeSlug === stack.slug
					? 'ring-2 ring-primary'
					: ''}"
			>
				<div class="flex-1 space-y-2 p-4">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<h3 class="flex items-center gap-1.5 truncate text-sm font-semibold">
								<Layers class="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
								<span class="truncate">{stack.name || stack.slug}</span>
							</h3>
							<p class="font-mono text-[11px] text-muted-foreground">{stack.slug}@v{stack.version}</p>
						</div>
						<Badge variant={stack.has_frontend ? 'default' : 'secondary'} class="shrink-0 {stack.has_frontend ? 'text-success-fg' : ''}">
							{stack.has_frontend ? 'full-stack' : 'backend-only'}
						</Badge>
					</div>
					<div class="flex flex-wrap gap-1">
						{#if stack.is_builtin}
							<Badge variant="secondary">Builtin</Badge>
						{:else}
							<Badge variant="outline">Custom</Badge>
						{/if}
						<!-- Strict check: older API responses omit is_approved. -->
						{#if !stack.is_builtin && stack.is_approved === false}
							<Badge variant="outline" class="border-warning/30 bg-warning/10 text-warning-fg">
								Pending approval — only you can run it
							</Badge>
						{/if}
						{#each stack.aliases ?? [] as alias (alias)}
							<Badge variant="outline" class="font-mono">{alias}</Badge>
						{/each}
					</div>
					{#if stack.description}
						<p class="line-clamp-2 text-xs text-muted-foreground">{stack.description}</p>
					{/if}
				</div>
				<div class="flex flex-wrap gap-1.5 border-t px-4 py-2.5">
					{#if canChange(stack) && onEdit}
						<Button variant="outline" size="sm" title="Edit as a new version" onclick={() => onEdit(stack)}>
							<Pencil /> Edit
						</Button>
					{/if}
					{#if onDuplicate}
						<Button
							variant="outline"
							size="sm"
							title="Duplicate as a new stack"
							aria-label="Duplicate {stack.slug}"
							onclick={() => onDuplicate(stack)}
						>
							<Copy />{#if stack.is_builtin}<span>Duplicate</span>{/if}
						</Button>
					{/if}
					{#if canChange(stack) && onArchive}
						<Button
							variant="outline"
							size="sm"
							class="hover:bg-destructive/10 hover:text-danger-fg"
							title="Archive all versions (jobs keep their pinned snapshots)"
							aria-label="Archive {stack.slug}"
							onclick={() => onArchive(stack)}
						>
							<Archive />
						</Button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
{/if}
