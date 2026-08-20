<script lang="ts">
	import { page } from '$app/state';
	import { authClient } from '$lib/auth-client';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';

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

<main class="flex min-h-screen items-center justify-center p-6">
	<Card.Root class="w-full max-w-md">
		<Card.Header>
			<Card.Title class="text-xl">Authorize access</Card.Title>
		</Card.Header>
		<Card.Content class="flex flex-col gap-4">
			<p class="text-muted-foreground">
				An application {#if clientId}(client <code>{clientId}</code>){/if} is requesting access to
				your account.
			</p>
			{#if scopes.length}
				<div>
					<p class="mb-1 text-muted-foreground">Requested scopes:</p>
					<ul class="list-disc pl-5">
						{#each scopes as scope}
							<li><code>{scope}</code></li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if error}
				<p class="text-sm text-destructive">{error}</p>
			{/if}
			<div class="flex gap-3">
				<Button onclick={() => decide(true)} disabled={loading}>Allow</Button>
				<Button variant="secondary" onclick={() => decide(false)} disabled={loading}>Deny</Button>
			</div>
		</Card.Content>
	</Card.Root>
</main>
