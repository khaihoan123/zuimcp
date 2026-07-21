<script lang="ts">
	import { goto } from '$app/navigation';
	import { invalidateAll } from '$app/navigation';
	import { authClient } from '$lib/auth-client';

	let { data } = $props();

	async function signOut() {
		await authClient.signOut();
		await invalidateAll();
		goto('/login');
	}
</script>

<main>
	<h1>MCP Platform</h1>

	{#if data.user}
		<p>Signed in as <strong>{data.user.email}</strong> ({data.user.name})</p>
		<button onclick={signOut}>Sign out</button>
	{:else}
		<p>You are not signed in.</p>
		<p><a href="/login">Sign in</a> or <a href="/signup">create an account</a></p>
	{/if}
</main>

<style>
	main {
		max-width: 32rem;
		margin: 4rem auto;
		font-family: system-ui, sans-serif;
	}
</style>
