<script lang="ts">
	import { onMount } from 'svelte';
	import ConnectorIcon from '$lib/components/connector-icon.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { capabilityLabel, connectorFor, serviceCategories, sortPermissions } from '$lib/config';

	type Permission = { capability: string; allowed: boolean };
	type Connection = {
		id: number;
		service_type: string;
		display_name: string;
		status: string;
		/** The mailbox address, or the Odoo login, host and database. */
		identity: string | null;
		permissions: Permission[];
	};

	/** Human labels for a connection's `status`. */
	const statusLabels: Record<string, string> = {
		active: 'active',
		disabled: 'disabled',
		needs_reauth: 'needs reconnecting'
	};

	let connections = $state<Connection[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	let renamingId = $state<number | null>(null);
	let renameValue = $state('');

	/** Groups accounts under the service category their connector belongs to. */
	let grouped = $derived(
		serviceCategories
			.map((category) => ({
				category,
				items: connections.filter((c) =>
					category.connectors.some((connector) => connector.id === c.service_type)
				)
			}))
			.filter((group) => group.items.length > 0)
	);

	/** Accounts whose service_type matches no known connector — never hide them. */
	let ungrouped = $derived(
		connections.filter((c) => !connectorFor(c.service_type))
	);

	async function loadConnections() {
		loading = true;
		loadError = '';
		try {
			const res = await fetch('/api/connections');
			if (!res.ok) throw new Error('Failed to load connections');
			connections = await res.json();
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadConnections();
	});

	async function togglePermission(conn: Connection, capability: string, allowed: boolean) {
		const previous = conn.permissions.map((p) => ({ ...p }));
		conn.permissions = conn.permissions.map((p) =>
			p.capability === capability ? { ...p, allowed } : p
		);
		try {
			const res = await fetch(`/api/connections/${conn.id}/permissions`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ permissions: { [capability]: allowed } })
			});
			if (!res.ok) throw new Error('Failed to update permission');
		} catch {
			conn.permissions = previous;
		}
	}

	function startRename(conn: Connection) {
		renamingId = conn.id;
		renameValue = conn.display_name;
	}

	async function saveRename(conn: Connection) {
		const newName = renameValue.trim();
		renamingId = null;
		if (!newName || newName === conn.display_name) return;
		const previous = conn.display_name;
		conn.display_name = newName;
		try {
			const res = await fetch(`/api/connections/${conn.id}`, {
				method: 'PATCH',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ display_name: newName })
			});
			if (!res.ok) throw new Error('Failed to rename');
		} catch {
			conn.display_name = previous;
		}
	}

	async function deleteConnection(conn: Connection) {
		if (!confirm(`Disconnect "${conn.display_name}"? Your AI agent will lose access immediately.`)) {
			return;
		}
		const res = await fetch(`/api/connections/${conn.id}`, { method: 'DELETE' });
		if (res.ok) {
			connections = connections.filter((c) => c.id !== conn.id);
		}
	}
</script>

<svelte:head>
	<title>Connected accounts</title>
</svelte:head>

{#snippet accountCard(conn: Connection)}
	{@const connector = connectorFor(conn.service_type)}
	<Card.Root>
		<Card.Content class="flex flex-col gap-4">
			<div class="flex items-center gap-3">
				{#if connector}
					<ConnectorIcon {connector} />
				{/if}
				<div class="min-w-0 flex-1">
					{#if renamingId === conn.id}
						<Input
							type="text"
							class="h-7 w-48"
							bind:value={renameValue}
							onblur={() => saveRename(conn)}
							onkeydown={(e) => e.key === 'Enter' && saveRename(conn)}
						/>
					{:else}
						<button class="truncate font-medium hover:underline" onclick={() => startRename(conn)}>
							{conn.display_name}
						</button>
					{/if}
					{#if conn.identity}
						<p class="truncate text-sm" title={conn.identity}>{conn.identity}</p>
					{/if}
					<p class="text-sm text-muted-foreground">
						{connector?.label ?? conn.service_type} ·
						<span
							class={conn.status === 'active'
								? 'text-green-600 dark:text-green-400'
								: conn.status === 'needs_reauth'
									? 'text-destructive'
									: ''}
						>
							{statusLabels[conn.status] ?? conn.status}
						</span>
					</p>
				</div>
				{#if conn.status === 'needs_reauth' && connector?.oauthStart}
					<!-- A plain link: OAuth needs a full-page visit to the provider. -->
					<Button size="sm" href={`${connector.oauthStart}?account=${conn.id}`} data-sveltekit-reload>
						Reconnect
					</Button>
				{/if}
				<Button variant="destructive" size="sm" onclick={() => deleteConnection(conn)}>
					Disconnect
				</Button>
			</div>

			<div class="border-t border-border pt-4">
				<p class="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">
					What your agent may do
				</p>
				<div class="flex flex-wrap gap-x-5 gap-y-2">
					{#each sortPermissions(conn.permissions) as perm (perm.capability)}
						<Label class="text-sm font-normal">
							<Checkbox
								checked={perm.allowed}
								onCheckedChange={(checked) =>
									togglePermission(conn, perm.capability, checked === true)}
							/>
							{capabilityLabel(perm.capability)}
						</Label>
					{/each}
				</div>
				{#if conn.status === 'needs_reauth'}
					<p class="mt-3 text-sm text-destructive">
						The provider no longer accepts this account's saved login (access was removed, or it
						expired). Your agent can't use it until you reconnect it.
					</p>
				{:else if !conn.permissions.some((p) => p.allowed)}
					<p class="mt-3 text-sm text-muted-foreground">
						Nothing granted yet — your agent can see this account but can't do anything with it.
					</p>
				{/if}
			</div>
		</Card.Content>
	</Card.Root>
{/snippet}

<div class="mx-auto max-w-4xl">
	<div class="flex items-center justify-between gap-4">
		<div>
			<h1 class="text-2xl font-semibold">Connected accounts</h1>
			<p class="mt-2 text-sm text-muted-foreground">
				Rename, adjust what your agent may do, or disconnect.
			</p>
		</div>
		<Button href="/dashboard/connections" variant="outline">+ Add account</Button>
	</div>

	{#if loading}
		<p class="mt-8 text-sm text-muted-foreground">Loading…</p>
	{:else if loadError}
		<p class="mt-8 text-sm text-destructive">{loadError}</p>
	{:else if connections.length === 0}
		<Card.Root class="mt-8">
			<Card.Content class="flex flex-col items-center gap-4 py-12 text-center">
				<p class="font-medium">No accounts connected yet</p>
				<p class="max-w-sm text-sm text-muted-foreground">
					Link an account from the connector catalogue. Until then your agent can't reach anything.
				</p>
				<Button href="/dashboard/connections">Browse connectors</Button>
			</Card.Content>
		</Card.Root>
	{:else}
		{#each grouped as group (group.category.id)}
			<section class="mt-8">
				<div class="mb-3 flex items-center gap-2">
					<h2 class="text-sm font-medium tracking-wide text-muted-foreground uppercase">
						{group.category.label}
					</h2>
					<Badge variant="outline">{group.items.length}</Badge>
				</div>
				<ul class="flex flex-col gap-4">
					{#each group.items as conn (conn.id)}
						<li>{@render accountCard(conn)}</li>
					{/each}
				</ul>
			</section>
		{/each}

		{#if ungrouped.length > 0}
			<section class="mt-8">
				<h2 class="mb-3 text-sm font-medium tracking-wide text-muted-foreground uppercase">
					Other
				</h2>
				<ul class="flex flex-col gap-4">
					{#each ungrouped as conn (conn.id)}
						<li>{@render accountCard(conn)}</li>
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>
