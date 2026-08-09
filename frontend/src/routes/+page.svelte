<script lang="ts">
	import { Button } from '$lib/components/ui/button';

	let { data } = $props();
</script>

<main class="mx-auto max-w-xl px-6 py-16 text-center">
	{#if data.user}
		<h1 class="mb-2 text-3xl font-semibold">Welcome back</h1>
		<p class="mb-8 text-muted-foreground">Manage the accounts your AI agent can access.</p>
		<Button href="/connections" size="lg">Go to Connections →</Button>
	{:else}
		<h1 class="mb-2 text-3xl font-semibold">MCP Platform</h1>
		<p class="mb-8 text-muted-foreground">
			Connect your email accounts and control exactly what your AI agent can do with them.
		</p>
		<div class="flex items-center justify-center gap-5">
			<Button href="/signup" size="lg">Get started</Button>
			<a class="text-muted-foreground hover:text-foreground" href="/login">Sign in</a>
		</div>
	{/if}
</main>
