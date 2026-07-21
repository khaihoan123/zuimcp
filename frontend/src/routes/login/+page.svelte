<script lang="ts">
	import { goto } from "$app/navigation";
	import { authClient } from "$lib/auth-client";

	let email = $state("");
	let password = $state("");
	let error = $state("");
	let loading = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = "";
		loading = true;
		const { error: err } = await authClient.signIn.email({
			email,
			password,
		});
		loading = false;
		if (err) {
			error = err.message ?? "Sign in failed";
			return;
		}
		goto("/");
	}
</script>

<main>
	<h1>Sign in</h1>
	<form onsubmit={submit}>
		<label>
			Email
			<input
				type="email"
				bind:value={email}
				required
				autocomplete="email"
			/>
		</label>
		<label>
			Password
			<input
				type="password"
				bind:value={password}
				required
				autocomplete="current-password"
			/>
		</label>
		{#if error}
			<p class="error">{error}</p>
		{/if}
		<button type="submit" disabled={loading}
			>{loading ? "Signing in…" : "Sign in"}</button
		>
	</form>
	<p>No account? <a href="/signup">Sign up</a></p>
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
