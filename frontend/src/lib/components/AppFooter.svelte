<script lang="ts">
	const links = [
		{ href: '/about', label: 'About' },
		{ href: '/privacy', label: 'Privacy' },
		{ href: '/docs', label: 'Docs' },
	];
</script>

<footer class="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border/70 px-4 py-3 font-mono text-[11px] text-muted-foreground sm:px-5">
	<div class="flex flex-wrap items-center gap-x-3 gap-y-1">
		<p>&copy; {new Date().getFullYear()} LLM Lab</p>
		<nav aria-label="Footer" class="flex items-center gap-3">
			{#each links as link (link.href)}
				<a
					href={link.href}
					class="rounded-sm transition-colors duration-150 hover:text-foreground focus-visible:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
				>{link.label}</a>
			{/each}
		</nav>
	</div>
	<p class="hidden uppercase tracking-[0.12em] sm:block">Research platform for LLM code evaluation</p>
</footer>
