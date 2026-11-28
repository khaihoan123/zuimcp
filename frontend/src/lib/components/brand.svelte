<script lang="ts">
	import { SITE_NAME } from "$lib/config";
	import LogoMark from "$lib/components/logo-mark.svelte";

	let {
		href = "/",
		class: className = "",
	}: { href?: string; class?: string } = $props();
</script>

<a
	class="flex items-center gap-2 font-semibold tracking-tight {className}"
	{href}
>
	<LogoMark class="size-7 shadow-sm shadow-primary/30 rounded-lg" />
	{SITE_NAME}
</a>
