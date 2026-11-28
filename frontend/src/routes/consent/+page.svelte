<script lang="ts">
	import { page } from '$app/state';
	import { authClient } from '$lib/auth-client';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import LogoMark from '$lib/components/logo-mark.svelte';
	import { SITE_NAME } from '$lib/config';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ArrowLeftRightIcon from '@lucide/svelte/icons/arrow-left-right';

	let { data } = $props();

	const appName = $derived(data.client.name ?? 'An app');
	const staysSignedIn = page.url.searchParams.get('scope')?.split(' ').includes('offline_access');

	let logoFailed = $state(false);
	let error = $state('');
	let loading = $state<'allow' | 'deny' | null>(null);

	async function decide(accept: boolean) {
		error = '';
		loading = accept ? 'allow' : 'deny';
		// The server verifies this signed query string (sig/exp params from
		// the /oauth2/authorize redirect) to recover which authorization
		// request this consent belongs to — it isn't tracked any other way.
		const oauth_query = page.url.search.slice(1);
		const { data: res, error: err } = await authClient.oauth2.consent({ accept, oauth_query });
		if (err) {
			loading = null;
			error = err.message ?? 'Something went wrong';
			return;
		}
		// On success the server returns the redirect back to the OAuth client.
		if (res?.url) {
			window.location.href = res.url;
		} else {
			loading = null;
		}
	}
</script>

<svelte:head>
	<title>Connect {appName} to {SITE_NAME}</title>
</svelte:head>

<main class="flex min-h-screen items-center justify-center p-6">
	<Card.Root class="w-full max-w-md">
		<Card.Header class="items-center text-center">
			<div class="mb-2 flex items-center justify-center gap-3">
				{#if data.client.logo && !logoFailed}
					<img
						src={data.client.logo}
						alt=""
						class="size-12 rounded-xl border border-border bg-background object-contain p-1"
						referrerpolicy="no-referrer"
						onerror={() => (logoFailed = true)}
					/>
				{:else}
					<span
						class="grid size-12 place-items-center rounded-xl border border-border bg-muted text-lg font-semibold"
					>
						{appName.charAt(0).toUpperCase()}
					</span>
				{/if}
				<ArrowLeftRightIcon class="size-4 text-muted-foreground" />
				<LogoMark class="size-12 rounded-xl" />
			</div>
			<Card.Title class="text-xl">{appName} wants to connect to {SITE_NAME}</Card.Title>
			{#if page.data.user?.email}
				<Card.Description>
					Signed in as <span class="font-medium text-foreground">{page.data.user.email}</span>
				</Card.Description>
			{/if}
		</Card.Header>

		<Card.Content class="flex flex-col gap-5">
			<div>
				<p class="mb-2 text-sm font-medium">This will let {appName}:</p>
				<ul class="flex flex-col gap-2 text-sm text-muted-foreground">
					<li class="flex gap-2">
						<CheckIcon class="mt-0.5 size-4 shrink-0 text-primary" />
						<span>
							Use the actions you've turned on for your connected accounts. You can change them
							anytime in the <a class="text-primary underline-offset-4 hover:underline" href="/dashboard"
								>dashboard</a
							>.
						</span>
					</li>
					{#if staysSignedIn}
						<li class="flex gap-2">
							<CheckIcon class="mt-0.5 size-4 shrink-0 text-primary" />
							<span>Stay connected, so you don't have to sign in again each time.</span>
						</li>
					{/if}
				</ul>
			</div>

			{#if data.redirectHost}
				<p class="rounded-lg bg-muted px-3 py-2 text-xs text-muted-foreground">
					After you allow, you'll go back to
					<span class="font-medium text-foreground">{data.redirectHost}</span>. Only allow apps you
					trust.
				</p>
			{/if}

			{#if error}
				<p class="text-sm text-destructive">{error}</p>
			{/if}

			<div class="flex flex-col gap-2 sm:flex-row-reverse">
				<Button class="flex-1" onclick={() => decide(true)} disabled={loading !== null}>
					{loading === 'allow' ? 'Connecting…' : 'Allow'}
				</Button>
				<Button
					class="flex-1"
					variant="secondary"
					onclick={() => decide(false)}
					disabled={loading !== null}
				>
					Cancel
				</Button>
			</div>
		</Card.Content>
	</Card.Root>
</main>
