<script lang="ts">
	/**
	 * StacksList — stack cards. Every stack can be duplicated; user stacks can
	 * be edited (new version) and archived by their owner and by staff
	 * (`canModerate`), as the server allows — anyone else sees another user's
	 * approved stack listed (they may run it) without those actions. Builtin
	 * stacks are shipped code: never edited or archived. A user stack awaiting
	 * approval runs only for its owner.
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
		/** Unused since the list layout adapts via a container query; kept for callers. */
		compact?: boolean;
		activeSlug?: string | null;
		/** Staff: may edit and archive every user stack, not only their own. */
		canModerate?: boolean;
		onEdit?: (s: Stack) => void;
		onDuplicate?: (s: Stack) => void;
		onArchive?: (s: Stack) => void;
	}
	let {
		stacks,
		loading,
		compact: _compact = false,
		activeSlug = null,
		canModerate = false,
		onEdit,
		onDuplicate,
		onArchive,
	}: Props = $props();

	// `is_own` is newer than some API responses: a missing field means "not yours".
	function canChange(stack: Stack): boolean {
		return !stack.is_builtin && ((stack as Stack & { is_own?: boolean }).is_own === true || canModerate);
	}
</script>

{#if loading}
	<LoadingState label="loading stacks" />
{:else if stacks.length === 0}
	<EmptyState icon={Layers} title="No stacks found" description="Clear the search, or run migrations to seed the builtin stacks." />
{:else}
	<div class="data-list @container">
		<div class="data-list-head" aria-hidden="true">
			<span class="flex-1">Stack</span>
			<span class="hidden w-28 @xl:block">Kind</span>
			<span class="w-[8.5rem]"></span>
		</div>
		{#each stacks as stack (stack.slug)}
			<div class="data-list-row" data-active={activeSlug === stack.slug}>
				<div class="flex items-center gap-4">
					<div class="min-w-0 flex-1">
						<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
							<Layers class="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
							<span class="text-sm font-medium">{stack.name || stack.slug}</span>
							<span class="font-mono text-[11px] text-muted-foreground">{stack.slug}@v{stack.version}</span>
							{#if stack.is_builtin}
								<Badge variant="secondary" class="h-5 px-1.5 text-[11px]">Builtin</Badge>
							{:else}
								<Badge variant="outline" class="h-5 px-1.5 text-[11px]">Custom</Badge>
							{/if}
							<!-- Strict check: older API responses omit is_approved. -->
							{#if !stack.is_builtin && stack.is_approved === false}
								<Badge variant="outline" class="h-5 border-warning/30 bg-warning/10 px-1.5 text-[11px] text-warning-fg">
									Pending approval — only you can run it
								</Badge>
							{/if}
							{#each stack.aliases ?? [] as alias (alias)}
								<Badge variant="outline" class="h-5 px-1.5 font-mono text-[11px]">{alias}</Badge>
							{/each}
						</div>
						{#if stack.description}
							<p class="cell-sub mt-0.5 line-clamp-1 text-xs text-muted-foreground" title={stack.description}>{stack.description}</p>
						{/if}
					</div>
					<!-- Text, not a green tint: green-on-tint is under 4.5:1 in light mode. -->
					<span class="hidden w-28 text-xs @xl:block {stack.has_frontend ? 'text-success-fg' : 'text-muted-foreground'}">
						{stack.has_frontend ? 'Full-stack' : 'Backend only'}
					</span>
					<div class="flex w-[8.5rem] shrink-0 items-center justify-end gap-0.5">
						{#if canChange(stack) && onArchive}
							<div class="row-actions-more">
								<Button
									variant="ghost"
									size="sm"
									class="size-8 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-danger-fg"
									title="Archive all versions (jobs keep their pinned snapshots)"
									aria-label="Archive {stack.slug}"
									onclick={() => onArchive(stack)}
								>
									<Archive />
								</Button>
							</div>
						{/if}
						{#if onDuplicate}
							<Button
								variant="ghost"
								size="sm"
								class="h-8 text-muted-foreground hover:text-foreground {canChange(stack) && onEdit ? 'w-8 p-0' : 'px-2'}"
								title="Duplicate as a new stack"
								aria-label="Duplicate {stack.slug}"
								onclick={() => onDuplicate(stack)}
							>
								<Copy />{#if !(canChange(stack) && onEdit)}<span>Duplicate</span>{/if}
							</Button>
						{/if}
						{#if canChange(stack) && onEdit}
							<Button variant="ghost" size="sm" class="h-8 px-2" title="Edit as a new version" onclick={() => onEdit(stack)}>
								<Pencil /> Edit
							</Button>
						{/if}
					</div>
				</div>
			</div>
		{/each}
	</div>
{/if}
