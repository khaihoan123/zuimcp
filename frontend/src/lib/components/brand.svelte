<script lang="ts">
	import { SITE_NAME } from "$lib/config";

	let {
		href = "/",
		class: className = "",
	}: { href?: string; class?: string } = $props();
</script>

<a
	class="flex items-center gap-2 font-semibold tracking-tight {className}"
	{href}
>
	<span
		class="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground"
	>
		<svg
			viewBox="0 0 24 24"
			fill="none"
			stroke="currentColor"
			stroke-width="2.5"
			class="size-4"
		>
			<path d="M4 7h16M4 12h10M4 17h7" stroke-linecap="round" />
		</svg>
	</span>
	{SITE_NAME}
</a>
