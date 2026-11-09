<script lang="ts">
	import { page } from '$app/state';
	import { goto, invalidateAll } from '$app/navigation';
	import { authClient } from '$lib/auth-client';
	import { SITE_NAME } from '$lib/config';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import LayoutDashboardIcon from '@lucide/svelte/icons/layout-dashboard';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import PlugIcon from '@lucide/svelte/icons/plug';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';
	import * as Avatar from '$lib/components/ui/avatar';
	import * as DropdownMenu from '$lib/components/ui/dropdown-menu';

	let { data, children } = $props();

	const navItems = [
		{ href: '/dashboard', label: 'Overview', icon: LayoutDashboardIcon, exact: true },
		{ href: '/dashboard/connections', label: 'Connections', icon: PlugIcon, exact: false },
		{
			href: '/dashboard/accounts',
			label: 'Connected accounts',
			icon: UserRoundIcon,
			exact: false
		},
		{ href: '/dashboard/activity', label: 'Activity', icon: HistoryIcon, exact: false }
	];

	let initials = $derived(
		(data.user?.name || data.user?.email || '?')
			.split(/[\s@._-]+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((part: string) => part[0]?.toUpperCase() ?? '')
			.join('')
	);

	async function signOut() {
		await authClient.signOut();
		await invalidateAll();
		goto('/');
	}
</script>

<div class="flex min-h-screen flex-col">
	<header class="border-b border-border bg-card">
		<div class="flex h-16 items-center gap-4 px-6">
			<a class="flex items-center gap-2 font-semibold tracking-tight" href="/">
				<span class="grid size-7 place-items-center rounded-lg bg-primary text-primary-foreground">
					<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="size-4">
						<path d="M4 7h16M4 12h10M4 17h7" stroke-linecap="round" />
					</svg>
				</span>
				{SITE_NAME}
			</a>
			<div class="flex-1"></div>

			<DropdownMenu.Root>
				<DropdownMenu.Trigger
					class="flex items-center gap-2 rounded-full p-1 pr-2.5 transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
					aria-label="Account menu"
				>
					<Avatar.Root size="sm">
						<Avatar.Fallback class="bg-primary text-xs text-primary-foreground">
							{initials}
						</Avatar.Fallback>
					</Avatar.Root>
					<span class="hidden text-sm text-muted-foreground sm:inline">{data.user?.email}</span>
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="end" class="w-56">
					<div class="px-1.5 py-1.5">
						{#if data.user?.name}
							<p class="truncate text-sm font-medium">{data.user.name}</p>
						{/if}
						<p class="truncate text-xs text-muted-foreground">{data.user?.email}</p>
					</div>
					<DropdownMenu.Separator />
					<DropdownMenu.Item variant="destructive" onSelect={signOut}>
						<LogOutIcon />
						Sign out
					</DropdownMenu.Item>
				</DropdownMenu.Content>
			</DropdownMenu.Root>
		</div>
	</header>

	<div class="flex flex-1 flex-col md:flex-row">
		<aside class="shrink-0 border-b border-border md:w-56 md:border-r md:border-b-0">
			<nav class="flex gap-1 p-3 md:flex-col">
				{#each navItems as item (item.href)}
					{@const active = item.exact
						? page.url.pathname === item.href
						: page.url.pathname.startsWith(item.href)}
					<a
						href={item.href}
						class="flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors {active
							? 'bg-muted text-foreground'
							: 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'}"
						aria-current={active ? 'page' : undefined}
					>
						<item.icon class="size-4" />
						{item.label}
					</a>
				{/each}
			</nav>
		</aside>

		<main class="min-w-0 flex-1">
			<div class="mx-auto w-full max-w-7xl px-6 py-10">
				{@render children()}
			</div>
		</main>
	</div>
</div>
