<script lang="ts">
	// /eval/t/[taskId]/a/[assignmentId] — the participant workbench for one
	// assignment. Keyed on the assignment so "Submit → next app" starts fresh.
	import Workbench from '$lib/components/eval-portal/workbench/Workbench.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

{#key data.assignmentId}
	<Workbench taskId={data.taskId} assignmentId={data.assignmentId} />
{/key}
