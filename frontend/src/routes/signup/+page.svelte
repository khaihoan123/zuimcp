<script lang="ts">
	import { goto } from '$app/navigation';
	import { authClient } from '$lib/auth-client';

	let name = $state('');
	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		loading = true;
		const { error: err } = await authClient.signUp.email({ name, email, password });
		loading = false;
		if (err) {
			error = err.message ?? 'Sign up failed';
			return;
		}
		goto('/');
	}
</script>

<main>
	<h1>Create account</h1>
	<form onsubmit={submit}>
		<label>
			Name
			<input type="text" bind:value={name} required autocomplete="name" />
		</label>
		<label>
			Email
			<input type="email" bind:value={email} required autocomplete="email" />
		</label>
		<label>
			Password
			<input type="password" bind:value={password} required minlength="8" autocomplete="new-password" />
		</label>
		{#if error}
			<p class="error">{error}</p>
		{/if}
		<button type="submit" disabled={loading}>{loading ? 'Creating…' : 'Sign up'}</button>
	</form>
	<p>Already have an account? <a href="/login">Sign in</a></p>
</main>

<style>
	main {
		max-width: 20rem;
		margin: 4rem auto;
		font-family: system-ui, sans-serif;
	}
	form {
		display: flex;
		flex-direction: column;
		gap: 0.75rem;
	}
	label {
		display: flex;
		flex-direction: column;
		gap: 0.25rem;
	}
	.error {
		color: #b00020;
		margin: 0;
	}
</style>
