<script lang="ts">
	import { onMount, tick } from 'svelte';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import PauseIcon from '@lucide/svelte/icons/pause';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import PlayIcon from '@lucide/svelte/icons/play';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import ConnectorIcon from '$lib/components/connector-icon.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import * as Dialog from '$lib/components/ui/dialog';
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
		/** Non-secret login settings to prefill the edit form (host, url, ...). */
		settings: Record<string, string | number>;
		permissions: Permission[];
	};

	/** Accounts with a login form (password / API key) rather than OAuth. */
	const editableLogin = new Set(['imap', 'odoo']);

	/** Human label for a connection's `status`. 'needs_reauth' is set when the
	 * provider refused the saved login, often during an AI call. */
	function statusLabel(conn: Connection): string {
		if (conn.status === 'needs_reauth') {
			return editableLogin.has(conn.service_type) ? 'login refused' : 'needs reconnecting';
		}
		return conn.status === 'disabled' ? 'paused' : conn.status;
	}

	/** Pause keeps the login and permissions but stops the agent using the account. */
	async function setPaused(conn: Connection, paused: boolean) {
		try {
			const updated = await request(`/api/connections/${conn.id}`, 'PATCH', {
				status: paused ? 'disabled' : 'active'
			});
			connections = connections.map((c) => (c.id === updated.id ? updated : c));
		} catch (err) {
			alert(err instanceof Error ? err.message : 'Could not change the account');
		}
	}

	/** Why an account stopped working, and what to do about it. */
	function refusedMessage(conn: Connection): string {
		if (conn.service_type === 'odoo') {
			return "Odoo refused this account's saved API key, so your agent can't use it. Keys often expire (Odoo preselects 1 day). Create a new key in Odoo, then click Update login.";
		}
		if (conn.service_type === 'imap') {
			return "The mail server refused this account's saved password, so your agent can't use it. If you changed the password or app password, click Update login.";
		}
		return "The provider no longer accepts this account's saved login (access was removed, or it expired). Your agent can't use it until you reconnect it.";
	}

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

	/** The account a link pointed at (#account-<id>, e.g. from Activity), briefly highlighted. */
	let highlightedId = $state<number | null>(null);

	onMount(async () => {
		await loadConnections();
		const id = Number(window.location.hash.match(/^#account-(\d+)$/)?.[1]);
		if (!id) return;
		highlightedId = id;
		await tick();
		document.getElementById(`account-${id}`)?.scrollIntoView({ block: 'center' });
		setTimeout(() => (highlightedId = null), 2500);
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

	// --- edit dialog --------------------------------------------------------

	let editing = $state<Connection | null>(null);
	let editOpen = $state(false);
	let editSaving = $state(false);
	let editError = $state('');
	let editName = $state('');
	let imapEdit = $state({ host: '', port: 993, smtp_host: '', smtp_port: 587, password: '' });
	let odooEdit = $state({ url: '', database: '', login: '', api_key: '' });

	function startEdit(conn: Connection) {
		const s = conn.settings ?? {};
		editing = conn;
		editName = conn.display_name;
		editError = '';
		imapEdit = {
			host: String(s.host ?? ''),
			port: Number(s.port ?? 993),
			smtp_host: String(s.smtp_host ?? ''),
			smtp_port: Number(s.smtp_port ?? 587),
			password: ''
		};
		odooEdit = {
			url: String(s.url ?? ''),
			database: String(s.database ?? ''),
			login: String(s.login ?? ''),
			api_key: ''
		};
		editOpen = true;
	}

	async function request(url: string, method: string, body: unknown): Promise<Connection> {
		const res = await fetch(url, {
			method,
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		});
		const data = await res.json().catch(() => ({}));
		if (!res.ok) throw new Error(data.detail ?? 'Could not save');
		return data;
	}

	/** Saves the name, then the login (the server checks it before storing it). */
	async function saveEdit(e: SubmitEvent) {
		e.preventDefault();
		const conn = editing;
		if (!conn) return;
		editSaving = true;
		editError = '';
		try {
			let updated: Connection | null = null;
			const name = editName.trim();
			if (name && name !== conn.display_name) {
				updated = await request(`/api/connections/${conn.id}`, 'PATCH', { display_name: name });
			}
			if (conn.service_type === 'imap') {
				updated = await request(`/api/connections/${conn.id}/imap`, 'PUT', imapEdit);
			} else if (conn.service_type === 'odoo') {
				updated = await request(`/api/connections/${conn.id}/odoo`, 'PUT', odooEdit);
			}
			if (updated) {
				const fresh = updated;
				connections = connections.map((c) => (c.id === fresh.id ? fresh : c));
			}
			editOpen = false;
		} catch (err) {
			editError = err instanceof Error ? err.message : 'Something went wrong';
			// A rename may have gone through before the login check failed.
			loadConnections();
		} finally {
			editSaving = false;
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
	<Card.Root
		class="transition-shadow duration-500 {highlightedId === conn.id ? 'ring-2 ring-primary' : ''}"
	>
		<Card.Content class="flex flex-col gap-4">
			<div class="flex flex-wrap items-center gap-3">
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
					<p class="flex items-center gap-1 text-sm text-muted-foreground">
						{connector?.label ?? conn.service_type} ·
						{#if conn.status === 'needs_reauth'}
							<span
								class="inline-flex items-center gap-1 rounded-md bg-amber-500/15 px-1.5 py-0.5 text-xs font-medium text-amber-700 dark:text-amber-400"
							>
								<TriangleAlertIcon class="size-3.5" />
								{statusLabel(conn)}
							</span>
						{:else}
							<span class={conn.status === 'active' ? 'text-green-600 dark:text-green-400' : ''}>
								{statusLabel(conn)}
							</span>
						{/if}
					</p>
				</div>
				{#if conn.status === 'needs_reauth' && connector?.oauthStart}
					<!-- A plain link: OAuth needs a full-page visit to the provider. -->
					<Button size="sm" href={`${connector.oauthStart}?account=${conn.id}`} data-sveltekit-reload>
						Reconnect
					</Button>
				{:else if conn.status === 'needs_reauth' && editableLogin.has(conn.service_type)}
					<Button size="sm" onclick={() => startEdit(conn)}>Update login</Button>
				{/if}
				<div class="flex flex-wrap items-center gap-2">
					<Button variant="ghost" size="sm" href={`/dashboard/activity?account=${conn.id}`}>
						<HistoryIcon />
						Activity
					</Button>
					{#if conn.status === 'active'}
						<Button variant="outline" size="sm" onclick={() => setPaused(conn, true)}>
							<PauseIcon />
							Pause
						</Button>
					{:else if conn.status === 'disabled'}
						<Button variant="outline" size="sm" onclick={() => setPaused(conn, false)}>
							<PlayIcon />
							Resume
						</Button>
					{/if}
					<Button variant="outline" size="sm" onclick={() => startEdit(conn)}>
						<PencilIcon />
						Edit
					</Button>
					<Button variant="destructive" size="sm" onclick={() => deleteConnection(conn)}>
						Disconnect
					</Button>
				</div>
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
					<p
						class="mt-3 flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-700 dark:text-amber-400"
					>
						<TriangleAlertIcon class="mt-0.5 size-4 shrink-0" />
						<span>{refusedMessage(conn)}</span>
					</p>
				{:else if conn.status === 'disabled'}
					<p class="mt-3 rounded-lg border border-border bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
						Paused: your agent can't use this account until you resume it. Its login and
						permissions are kept.
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
				Edit a name or login, choose what your agent may do, or disconnect.
			</p>
		</div>
		<Button href="/dashboard/connectors" variant="outline">+ Add account</Button>
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
				<Button href="/dashboard/connectors">Browse connectors</Button>
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
						<li id={`account-${conn.id}`} class="scroll-mt-24">{@render accountCard(conn)}</li>
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
						<li id={`account-${conn.id}`} class="scroll-mt-24">{@render accountCard(conn)}</li>
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>

<!-- Edit dialog: the name for every account, plus the login for password / API-key accounts. -->
<Dialog.Root bind:open={editOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>Edit {editing?.display_name ?? ''}</Dialog.Title>
			<Dialog.Description>
				{#if editing && editableLogin.has(editing.service_type)}
					We check the new login before saving it, and store it encrypted. Leave the
					{editing.service_type === 'odoo' ? 'API key' : 'password'} empty to keep the current one.
				{:else}
					This account signs in with its provider, so only the name can be changed here.
				{/if}
			</Dialog.Description>
		</Dialog.Header>

		<form class="flex flex-col gap-3" onsubmit={saveEdit}>
			<div class="flex flex-col gap-1.5">
				<Label for="edit_name">Display name</Label>
				<Input id="edit_name" type="text" bind:value={editName} required />
			</div>

			{#if editing?.service_type === 'odoo'}
				<div class="flex flex-col gap-1.5">
					<Label for="edit_odoo_url">Odoo URL</Label>
					<Input id="edit_odoo_url" type="url" bind:value={odooEdit.url} required />
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="edit_odoo_database">Database</Label>
					<Input
						id="edit_odoo_database"
						type="text"
						bind:value={odooEdit.database}
						required
						autocomplete="off"
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="edit_odoo_login">Login</Label>
					<Input
						id="edit_odoo_login"
						type="text"
						bind:value={odooEdit.login}
						required
						autocomplete="off"
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="edit_odoo_key">New API key</Label>
					<Input
						id="edit_odoo_key"
						type="password"
						bind:value={odooEdit.api_key}
						placeholder="Leave empty to keep the current key"
						autocomplete="off"
					/>
					<p class="text-xs text-muted-foreground">
						In Odoo: your avatar → My Profile (or Preferences) → Account Security → New API Key.
						Pick a long Duration so it doesn't expire tomorrow.
					</p>
				</div>
			{:else if editing?.service_type === 'imap'}
				<div class="flex flex-col gap-1">
					<p class="text-sm font-medium">Email address</p>
					<p class="text-sm">{editing.settings?.email ?? editing.identity}</p>
					<p class="text-xs text-muted-foreground">
						A different address is a different mailbox: add it as a new account instead.
					</p>
				</div>
				<div class="flex gap-3">
					<div class="flex flex-1 flex-col gap-1.5">
						<Label for="edit_host">IMAP host</Label>
						<Input id="edit_host" type="text" bind:value={imapEdit.host} required />
					</div>
					<div class="flex flex-col gap-1.5">
						<Label for="edit_port">Port</Label>
						<Input id="edit_port" type="number" bind:value={imapEdit.port} required class="w-24" />
					</div>
				</div>
				<div class="flex gap-3">
					<div class="flex flex-1 flex-col gap-1.5">
						<Label for="edit_smtp_host">SMTP host</Label>
						<Input id="edit_smtp_host" type="text" bind:value={imapEdit.smtp_host} required />
					</div>
					<div class="flex flex-col gap-1.5">
						<Label for="edit_smtp_port">Port</Label>
						<Input
							id="edit_smtp_port"
							type="number"
							bind:value={imapEdit.smtp_port}
							required
							class="w-24"
						/>
					</div>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="edit_password">New password</Label>
					<Input
						id="edit_password"
						type="password"
						bind:value={imapEdit.password}
						placeholder="Leave empty to keep the current password"
						autocomplete="off"
					/>
				</div>
			{/if}

			{#if editError}
				<p class="text-sm text-destructive">{editError}</p>
			{/if}
			<div class="mt-1 flex justify-end gap-2">
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} type="button" variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={editSaving}>
					{editSaving ? 'Checking…' : 'Save'}
				</Button>
			</div>
		</form>
	</Dialog.Content>
</Dialog.Root>
