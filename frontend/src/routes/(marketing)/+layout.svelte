<script lang="ts">
	import { page } from '$app/state';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import MenuIcon from '@lucide/svelte/icons/menu';
	import XIcon from '@lucide/svelte/icons/x';
	import Brand from '$lib/components/brand.svelte';
	import { Button } from '$lib/components/ui/button';
	import * as DropdownMenu from '$lib/components/ui/dropdown-menu';
	import { SITE_NAME, marketingNav, products } from '$lib/config';

	let { data, children } = $props();

	let mobileOpen = $state(false);

	// Close the mobile panel whenever navigation lands on a new page.
	$effect(() => {
		page.url.pathname;
		mobileOpen = false;
	});
</script>

<div class="flex min-h-screen flex-col">
	<header
		class="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60"
	>
		<nav class="mx-auto flex h-16 w-full max-w-6xl items-center gap-6 px-6">
			<Brand />

			<!-- Desktop nav -->
			<div class="hidden flex-1 items-center gap-1 md:flex">
				<DropdownMenu.Root>
					<DropdownMenu.Trigger
						class="flex items-center gap-1 rounded-lg px-3 py-2 text-sm font-medium text-muted-foreground transition-colors outline-none hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 data-[state=open]:text-foreground"
					>
						Products
						<ChevronDownIcon class="size-4" />
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="start" class="w-72">
						{#each products as product (product.label)}
							{#if product.href}
								<DropdownMenu.Item>
									{#snippet child({ props })}
										<a
											{...props}
											href={product.href}
											class="{props.class} flex-col items-start gap-0.5 py-2"
										>
											<span class="font-medium">{product.label}</span>
											<span class="text-xs text-muted-foreground">{product.description}</span>
										</a>
									{/snippet}
								</DropdownMenu.Item>
							{:else}
								<!-- No page yet — shown but not clickable. -->
								<div class="flex flex-col gap-0.5 px-1.5 py-2">
									<span class="flex items-center gap-2 text-sm font-medium text-muted-foreground">
										{product.label}
										<span
											class="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase"
										>
											Coming soon
										</span>
									</span>
									<span class="text-xs text-muted-foreground">{product.description}</span>
								</div>
							{/if}
						{/each}
					</DropdownMenu.Content>
				</DropdownMenu.Root>

				{#each marketingNav as item (item.href)}
					<a
						href={item.href}
						class="rounded-lg px-3 py-2 text-sm font-medium transition-colors {page.url.pathname.startsWith(
							item.href
						)
							? 'text-foreground'
							: 'text-muted-foreground hover:text-foreground'}"
					>
						{item.label}
					</a>
				{/each}
			</div>

			<div class="flex-1 md:hidden"></div>

			<!-- Desktop actions -->
			<div class="hidden items-center gap-4 md:flex">
				{#if data.user}
					<Button href="/dashboard" size="lg">Go to dashboard</Button>
				{:else}
					<a class="text-sm font-medium text-muted-foreground hover:text-foreground" href="/login">
						Sign in
					</a>
					<Button href="/signup" size="lg">Connect for free</Button>
				{/if}
			</div>

			<!-- Mobile toggle -->
			<button
				class="grid size-9 place-items-center rounded-lg text-muted-foreground transition-colors hover:bg-muted hover:text-foreground md:hidden"
				aria-label={mobileOpen ? 'Close menu' : 'Open menu'}
				aria-expanded={mobileOpen}
				onclick={() => (mobileOpen = !mobileOpen)}
			>
				{#if mobileOpen}
					<XIcon class="size-5" />
				{:else}
					<MenuIcon class="size-5" />
				{/if}
			</button>
		</nav>

		<!-- Mobile panel -->
		{#if mobileOpen}
			<div class="border-t border-border/60 bg-background px-6 py-4 md:hidden">
				<p class="px-1 pb-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
					Products
				</p>
				{#each products as product (product.label)}
					{#if product.href}
						<a href={product.href} class="block rounded-lg px-1 py-2 text-sm font-medium">
							{product.label}
						</a>
					{:else}
						<p class="flex items-center gap-2 px-1 py-2 text-sm font-medium text-muted-foreground">
							{product.label}
							<span
								class="rounded-full bg-muted px-1.5 py-0.5 text-[10px] tracking-wide uppercase"
							>
								Coming soon
							</span>
						</p>
					{/if}
				{/each}

				<div class="my-3 border-t border-border/60"></div>

				{#each marketingNav as item (item.href)}
					<a href={item.href} class="block rounded-lg px-1 py-2 text-sm font-medium">{item.label}</a>
				{/each}

				<div class="mt-4 flex flex-col gap-3">
					{#if data.user}
						<Button href="/dashboard" size="lg">Go to dashboard</Button>
					{:else}
						<Button href="/signup" size="lg">Connect for free</Button>
						<Button href="/login" variant="outline" size="lg">Sign in</Button>
					{/if}
				</div>
			</div>
		{/if}
	</header>

	<main class="flex-1">
		{@render children()}
	</main>

	<footer class="border-t border-border/60">
		<div class="mx-auto w-full max-w-6xl px-6 py-12">
			<div class="flex flex-col gap-8 sm:flex-row sm:justify-between">
				<div class="max-w-xs">
					<Brand />
					<p class="mt-3 text-sm text-muted-foreground">
						Connect your tools to AI agents, and control exactly what they can do.
					</p>
				</div>
				<div class="flex gap-12">
					<div>
						<p class="text-sm font-medium">Products</p>
						<ul class="mt-3 space-y-2">
							{#each products as product (product.label)}
								<li>
									{#if product.href}
										<a
											class="text-sm text-muted-foreground hover:text-foreground"
											href={product.href}
										>
											{product.label}
										</a>
									{:else}
										<span class="text-sm text-muted-foreground">
											{product.label} <span class="text-xs">(soon)</span>
										</span>
									{/if}
								</li>
							{/each}
						</ul>
					</div>
					<div>
						<p class="text-sm font-medium">Resources</p>
						<ul class="mt-3 space-y-2">
							{#each marketingNav as item (item.href)}
								<li>
									<a class="text-sm text-muted-foreground hover:text-foreground" href={item.href}>
										{item.label}
									</a>
								</li>
							{/each}
						</ul>
					</div>
				</div>
			</div>
			<p class="mt-10 border-t border-border/60 pt-6 text-sm text-muted-foreground">
				© {new Date().getFullYear()}
				{SITE_NAME}
			</p>
		</div>
	</footer>
</div>
