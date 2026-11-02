<script lang="ts">
	// Participant portal (/eval/**): no app sidebar and no login gate. Anonymous
	// link / code participants, evaluator accounts and researcher previews share it.
	import PortalShell from '$lib/components/eval-portal/PortalShell.svelte';

	let { children } = $props();
</script>

<PortalShell>
	{@render children()}
</PortalShell>
