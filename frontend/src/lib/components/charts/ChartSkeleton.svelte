<script lang="ts">
	interface Props {
		height?: number;
	}

	let { height = 208 }: Props = $props();
</script>

<div class="skeleton w-full" style="height: {height}px" aria-hidden="true"></div>
