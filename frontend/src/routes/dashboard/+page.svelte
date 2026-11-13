<script lang="ts">
	import { onMount } from 'svelte';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import ScrollTextIcon from '@lucide/svelte/icons/scroll-text';
	import ConnectorIcon from '$lib/components/connector-icon.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import {
		allConnectors,
		capabilityLabel,
		connectorFor,
		serviceCategories,
		sortPermissions
	} from '$lib/config';

	type Permission = { capability: string; allowed: boolean };
	type Connection = {
		id: number;
		service_type: string;
		display_name: string;
		status: string;
		identity: string | null;
		permissions: Permission[];
	};

	let { data } = $props();

	let connections = $state<Connection[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	let enabledCapabilities = $derived(
		connections.reduce((sum, c) => sum + c.permissions.filter((p) => p.allowed).length, 0)
	);
	let availableConnectors = $derived(
		allConnectors.filter((c) => c.availability === 'available').length
	);

	onMount(async () => {
		try {
			const res = await fetch('/api/connections');
			if (!res.ok) throw new Error('Failed to load connections');
			connections = await res.json();
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>Overview</title>
</svelte:head>

<div class="mx-auto max-w-4xl">
	<h1 class="text-2xl font-semibold">Overview</h1>
	<p class="mt-2 text-sm text-muted-foreground">
		What your AI agent can currently reach through {data.user?.email}.
	</p>

	<!-- Stats -->
	<div class="mt-8 grid gap-4 sm:grid-cols-3">
		<Card.Root>
			<Card.Content>
				<p class="text-sm text-muted-foreground">Connected accounts</p>
				<p class="mt-1 text-3xl font-semibold tabular-nums">
					{loading ? '—' : connections.length}
				</p>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Content>
				<p class="text-sm text-muted-foreground">Capabilities granted</p>
				<p class="mt-1 text-3xl font-semibold tabular-nums">
					{loading ? '—' : enabledCapabilities}
				</p>
			</Card.Content>
		</Card.Root>
		<Card.Root>
			<Card.Content>
				<p class="text-sm text-muted-foreground">Connectors available</p>
				<p class="mt-1 text-3xl font-semibold tabular-nums">{availableConnectors}</p>
				<p class="mt-1 text-xs text-muted-foreground">
					of {allConnectors.length} across {serviceCategories.length} categories
				</p>
			</Card.Content>
		</Card.Root>
	</div>

	<!-- Connected accounts -->
	<div class="mt-10 mb-3 flex items-center justify-between">
		<h2 class="text-sm font-medium tracking-wide text-muted-foreground uppercase">
			Connected accounts
		</h2>
		<Button href="/dashboard/accounts" variant="outline" size="sm">
			Manage
			<ArrowRightIcon />
		</Button>
	</div>

	{#if loading}
		<p class="text-sm text-muted-foreground">Loading…</p>
	{:else if loadError}
		<p class="text-sm text-destructive">{loadError}</p>
	{:else if connections.length === 0}
		<Card.Root>
			<Card.Content class="flex flex-col items-center gap-4 py-10 text-center">
				<p class="font-medium">Nothing connected yet</p>
				<p class="max-w-sm text-sm text-muted-foreground">
					Browse the connector catalogue to link your first account. Your agent can't reach anything
					until you do.
				</p>
				<Button href="/dashboard/connections">Browse connectors</Button>
			</Card.Content>
		</Card.Root>
	{:else}
		<ul class="flex flex-col gap-3">
			{#each connections as conn (conn.id)}
				{@const connector = connectorFor(conn.service_type)}
				<li>
					<Card.Root>
						<Card.Content class="flex items-center gap-4">
							{#if connector}
								<ConnectorIcon {connector} />
							{/if}
							<div class="min-w-0 flex-1">
								<p class="truncate font-medium">
									{conn.display_name}
									{#if conn.identity}
										<span class="font-normal text-muted-foreground">· {conn.identity}</span>
									{/if}
								</p>
								<p class="truncate text-sm text-muted-foreground">
									{#if conn.permissions.some((p) => p.allowed)}
										{sortPermissions(conn.permissions.filter((p) => p.allowed))
											.map((p) => capabilityLabel(p.capability))
											.join(' · ')}
									{:else}
										No capabilities granted — your agent can see this account but do nothing
									{/if}
								</p>
							</div>
							<Badge variant={conn.status === 'active' ? 'secondary' : 'outline'}>
								{conn.status}
							</Badge>
						</Card.Content>
					</Card.Root>
				</li>
			{/each}
		</ul>
	{/if}

	<!-- Activity: links out to the real audit log page instead of duplicating it here. -->
	<div class="mt-10 mb-3 flex items-center justify-between">
		<h2 class="text-sm font-medium tracking-wide text-muted-foreground uppercase">Activity</h2>
		<Button href="/dashboard/activity" variant="outline" size="sm">
			View all
			<ArrowRightIcon />
		</Button>
	</div>
	<Card.Root>
		<Card.Content class="flex items-start gap-4">
			<span class="grid size-10 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
				<ScrollTextIcon class="size-5" />
			</span>
			<div>
				<p class="font-medium">See what your agent has been doing</p>
				<p class="mt-1 text-sm text-muted-foreground">
					Every tool call your agent makes — which capability, and whether it succeeded — is logged
					on the Activity page.
				</p>
			</div>
		</Card.Content>
	</Card.Root>
</div>
