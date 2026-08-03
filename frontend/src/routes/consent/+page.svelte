<script lang="ts">
	import { page } from '$app/state';
	import { authClient } from '$lib/auth-client';

	const clientId = page.url.searchParams.get('client_id') ?? '';
	const scopes = (page.url.searchParams.get('scope') ?? '').split(' ').filter(Boolean);

	let error = $state('');
	let loading = $state(false);

	async function decide(accept: boolean) {
		error = '';
		loading = true;
		// The server verifies this signed query string (sig/exp params from
		// the /oauth2/authorize redirect) to recover which authorization
		// request this consent belongs to — it isn't tracked any other way.
		const oauth_query = page.url.search.slice(1);
		const { data, error: err } = await authClient.oauth2.consent({ accept, oauth_query });
		loading = false;
		if (err) {
			error = err.message ?? 'Something went wrong';
			return;
		}
		// On success the server returns the redirect back to the OAuth client.
		if (data?.url) {
			window.location.href = data.url;
		}
	}
</script>

<main>
	<h1>Authorize access</h1>
	<p>
		An application {#if clientId}(client <code>{clientId}</code>){/if} is requesting access to your
		account.
	</p>
	{#if scopes.length}
		<p>Requested scopes:</p>
		<ul>
			{#each scopes as scope}
				<li><code>{scope}</code></li>
			{/each}
		</ul>
	{/if}
	{#if error}
		<p class="error">{error}</p>
	{/if}
	<div class="actions">
		<button onclick={() => decide(true)} disabled={loading}>Allow</button>
		<button class="secondary" onclick={() => decide(false)} disabled={loading}>Deny</button>
	</div>
</main>

<style>
	main {
		max-width: 26rem;
		margin: 4rem auto;
		font-family: system-ui, sans-serif;
	}
	.actions {
		display: flex;
		gap: 0.75rem;
	}
	.secondary {
		background: transparent;
	}
	.error {
		color: #b00020;
	}
</style>
