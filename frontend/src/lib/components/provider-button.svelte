<script lang="ts">
	import { authClient } from '$lib/auth-client';

	let { provider }: { provider: 'google' | 'github' } = $props();

	let loading = $state(false);
	let error = $state('');

	const labels = { google: 'Google', github: 'GitHub' };

	async function handleClick() {
		error = '';
		loading = true;
		// On success this redirects the browser away, so there is nothing to
		// reset loading for — it only returns here if the request itself
		// failed (e.g. the provider isn't configured on the server).
		const { error: err } = await authClient.signIn.social({
			provider,
			callbackURL: '/dashboard'
		});
		if (err) {
			error = err.message ?? 'Could not start sign in';
			loading = false;
		}
	}
</script>

<div class="flex flex-col gap-1">
	<button
		type="button"
		onclick={handleClick}
		disabled={loading}
		class="inline-flex h-9 w-full items-center justify-center gap-2 rounded-md border border-input bg-background px-4 text-sm font-medium shadow-xs transition-colors hover:bg-accent hover:text-accent-foreground disabled:pointer-events-none disabled:opacity-50"
	>
		{#if provider === 'google'}
			<svg viewBox="0 0 24 24" class="size-4" aria-hidden="true">
				<path
					fill="#4285F4"
					d="M23.52 12.27c0-.85-.08-1.67-.22-2.45H12v4.63h6.48a5.54 5.54 0 0 1-2.4 3.63v3.02h3.88c2.27-2.09 3.56-5.17 3.56-8.83Z"
				/>
				<path
					fill="#34A853"
					d="M12 24c3.24 0 5.96-1.07 7.96-2.9l-3.88-3.02c-1.08.72-2.46 1.15-4.08 1.15-3.14 0-5.8-2.12-6.75-4.97H1.24v3.12A11.99 11.99 0 0 0 12 24Z"
				/>
				<path
					fill="#FBBC05"
					d="M5.25 14.26A7.14 7.14 0 0 1 4.86 12c0-.78.14-1.54.39-2.26V6.62H1.24A11.99 11.99 0 0 0 0 12c0 1.94.47 3.77 1.24 5.38l4.01-3.12Z"
				/>
				<path
					fill="#EA4335"
					d="M12 4.77c1.77 0 3.35.61 4.6 1.8l3.44-3.44C17.95 1.19 15.24 0 12 0 7.31 0 3.26 2.7 1.24 6.62l4.01 3.12C6.2 6.89 8.86 4.77 12 4.77Z"
				/>
			</svg>
		{:else}
			<svg viewBox="0 0 24 24" class="size-4" fill="currentColor" aria-hidden="true">
				<path
					d="M12 .5C5.73.5.5 5.73.5 12c0 5.08 3.29 9.39 7.86 10.91.57.1.79-.25.79-.55v-2.15c-3.2.7-3.87-1.35-3.87-1.35-.53-1.34-1.29-1.7-1.29-1.7-1.05-.72.08-.7.08-.7 1.16.08 1.77 1.19 1.77 1.19 1.03 1.76 2.7 1.25 3.36.95.1-.75.4-1.25.72-1.53-2.55-.29-5.23-1.28-5.23-5.68 0-1.25.45-2.28 1.19-3.08-.12-.29-.51-1.46.11-3.05 0 0 .97-.31 3.18 1.18a11 11 0 0 1 5.79 0c2.2-1.49 3.18-1.18 3.18-1.18.63 1.59.24 2.76.12 3.05.74.8 1.19 1.83 1.19 3.08 0 4.41-2.69 5.39-5.25 5.67.42.36.78 1.08.78 2.17v3.22c0 .3.21.65.79.55A11.5 11.5 0 0 0 23.5 12C23.5 5.73 18.27.5 12 .5Z"
				/>
			</svg>
		{/if}
		Continue with {labels[provider]}
	</button>
	{#if error}
		<p class="text-xs text-destructive">{error}</p>
	{/if}
</div>
