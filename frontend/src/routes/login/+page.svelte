<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { authClient } from '$lib/auth-client';
	import ArrowLeftIcon from '@lucide/svelte/icons/arrow-left';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		error = '';
		loading = true;
		const { error: err } = await authClient.signIn.email({ email, password });
		loading = false;
		if (err) {
			error = err.message ?? 'Sign in failed';
			return;
		}
		await invalidateAll();
		goto('/dashboard');
	}
</script>

<main class="flex min-h-screen flex-col items-center justify-center gap-6 p-6">
	<a
		class="flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
		href="/"
	>
		<ArrowLeftIcon class="size-4" />
		Back to home
	</a>

	<Card.Root class="w-full max-w-sm">
		<Card.Header>
			<Card.Title class="text-center text-xl">Sign in</Card.Title>
		</Card.Header>
		<Card.Content>
			<form class="flex flex-col gap-4" onsubmit={submit}>
				<div class="flex flex-col gap-1.5">
					<Label for="email">Email</Label>
					<Input id="email" type="email" bind:value={email} required autocomplete="email" />
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="password">Password</Label>
					<Input
						id="password"
						type="password"
						bind:value={password}
						required
						autocomplete="current-password"
					/>
				</div>
				{#if error}
					<p class="text-sm text-destructive">{error}</p>
				{/if}
				<Button type="submit" disabled={loading}>
					{loading ? 'Signing in…' : 'Sign in'}
				</Button>
				<p class="text-center text-sm text-muted-foreground">
					No account? <a class="text-primary underline-offset-4 hover:underline" href="/signup"
						>Sign up</a
					>
				</p>
			</form>
		</Card.Content>
	</Card.Root>
</main>
