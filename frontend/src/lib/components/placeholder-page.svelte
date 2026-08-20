<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import { Button } from '$lib/components/ui/button';

	let {
		title,
		blurb,
		note
	}: {
		title: string;
		blurb: string;
		/** What still needs to be written here, shown as a quiet note. */
		note?: string;
	} = $props();
</script>

<svelte:head>
	<title>{title}</title>
</svelte:head>

<section class="mx-auto w-full max-w-3xl px-6 py-24 text-center">
	<h1 class="text-4xl font-semibold tracking-tight">{title}</h1>
	<p class="mx-auto mt-4 max-w-xl text-lg text-muted-foreground">{blurb}</p>
	{#if note}
		<p class="mx-auto mt-6 max-w-md rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted-foreground">
			{note}
		</p>
	{/if}
	<div class="mt-8 flex justify-center">
		<Button href="/signup" size="lg">
			Connect for free
			<ArrowRightIcon />
		</Button>
	</div>
</section>
