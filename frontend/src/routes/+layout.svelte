<script lang="ts">
	import favicon from "$lib/assets/favicon.svg";
	import "$lib/styles/app.css";
	import { page } from "$app/state";
	import { goto, invalidateAll } from "$app/navigation";
	import { authClient } from "$lib/auth-client";
	import { Button } from "$lib/components/ui/button";

	let { data, children } = $props();

	const authPages = ["/login", "/signup", "/consent"];
	let showNav = $derived(!authPages.includes(page.url.pathname));

	async function signOut() {
		await authClient.signOut();
		await invalidateAll();
		goto("/login");
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

{#if showNav}
	<nav
		class="flex items-center gap-5 border-b border-border bg-card px-6 py-4"
	>
		<a class="font-bold text-foreground" href="/">MCP Platform</a>
		<div class="flex-1"></div>
		{#if data.user}
			<a
				class="text-sm text-muted-foreground hover:text-foreground"
				href="/connections">Connections</a
			>
			<span class="text-sm text-muted-foreground">{data.user.email}</span>
			<Button variant="secondary" size="lg" onclick={signOut}
				>Sign out</Button
			>
		{:else}
			<a
				class="text-sm text-muted-foreground hover:text-foreground"
				href="/login">Sign in</a
			>
			<a
				class="text-sm text-muted-foreground hover:text-foreground"
				href="/signup">Sign up</a
			>
		{/if}
	</nav>
{/if}

{@render children()}
