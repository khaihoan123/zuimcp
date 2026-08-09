<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { Checkbox } from '$lib/components/ui/checkbox';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';

	type Permission = { capability: string; allowed: boolean };
	type Connection = {
		id: number;
		service_type: string;
		display_name: string;
		status: string;
		permissions: Permission[];
	};

	const CAPABILITY_LABELS: Record<string, string> = {
		'email.read': 'Read emails',
		'email.search': 'Search emails',
		'email.mark_seen': 'Mark read / unread',
		'email.draft': 'Create drafts'
	};

	let connections = $state<Connection[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	let showAddForm = $state(false);
	let addSubmitting = $state(false);
	let addError = $state('');
	let form = $state({ display_name: '', host: '', port: 993, email: '', password: '' });

	let renamingId = $state<number | null>(null);
	let renameValue = $state('');

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

	async function addConnection(e: SubmitEvent) {
		e.preventDefault();
		addSubmitting = true;
		addError = '';
		try {
			const res = await fetch('/api/connections/imap', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(form)
			});
			const body = await res.json();
			if (!res.ok) throw new Error(body.detail ?? 'Could not connect');
			connections = [...connections, body];
			form = { display_name: '', host: '', port: 993, email: '', password: '' };
			showAddForm = false;
		} catch (e) {
			addError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			addSubmitting = false;
		}
	}

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

<main class="mx-auto max-w-2xl px-6 py-10">
	<div class="mb-6 flex items-center justify-between">
		<h1 class="text-2xl font-semibold">Connections</h1>
		<Button onclick={() => (showAddForm = !showAddForm)}>
			{showAddForm ? 'Cancel' : '+ Connect mailbox'}
		</Button>
	</div>

	{#if showAddForm}
		<Card.Root class="mb-6">
			<Card.Header>
				<Card.Title class="text-base">Connect an IMAP mailbox</Card.Title>
			</Card.Header>
			<Card.Content>
				<form class="flex flex-col gap-3" onsubmit={addConnection}>
					<div class="flex flex-col gap-1.5">
						<Label for="display_name">Display name</Label>
						<Input
							id="display_name"
							type="text"
							bind:value={form.display_name}
							placeholder="Work inbox"
							required
						/>
					</div>
					<div class="flex gap-3">
						<div class="flex flex-1 flex-col gap-1.5">
							<Label for="host">IMAP host</Label>
							<Input
								id="host"
								type="text"
								bind:value={form.host}
								placeholder="mail.example.com"
								required
							/>
						</div>
						<div class="flex flex-col gap-1.5">
							<Label for="port">Port</Label>
							<Input id="port" type="number" bind:value={form.port} required class="w-24" />
						</div>
					</div>
					<div class="flex flex-col gap-1.5">
						<Label for="email">Email address</Label>
						<Input id="email" type="email" bind:value={form.email} required autocomplete="off" />
					</div>
					<div class="flex flex-col gap-1.5">
						<Label for="password">Password</Label>
						<Input
							id="password"
							type="password"
							bind:value={form.password}
							required
							autocomplete="off"
						/>
					</div>
					{#if addError}
						<p class="text-sm text-destructive">{addError}</p>
					{/if}
					<Button type="submit" disabled={addSubmitting}>
						{addSubmitting ? 'Connecting…' : 'Connect'}
					</Button>
				</form>
			</Card.Content>
		</Card.Root>
	{/if}

	{#if loading}
		<p class="text-muted-foreground">Loading…</p>
	{:else if loadError}
		<p class="text-destructive">{loadError}</p>
	{:else if connections.length === 0}
		<p class="text-muted-foreground">No connected accounts yet. Click "Connect mailbox" to add one.</p>
	{:else}
		<ul class="flex flex-col gap-4">
			{#each connections as conn (conn.id)}
				<li>
					<Card.Root>
						<Card.Content class="flex flex-col gap-3">
							<div class="flex items-center justify-between">
								<div class="flex items-center gap-2.5">
									<span
										class="rounded-full bg-muted px-2 py-0.5 text-xs tracking-wide text-muted-foreground uppercase"
										>{conn.service_type}</span
									>
									{#if renamingId === conn.id}
										<Input
											type="text"
											class="h-7 w-40"
											bind:value={renameValue}
											onblur={() => saveRename(conn)}
											onkeydown={(e) => e.key === 'Enter' && saveRename(conn)}
										/>
									{:else}
										<button
											class="font-semibold text-foreground hover:underline"
											onclick={() => startRename(conn)}>{conn.display_name}</button
										>
									{/if}
									<span
										class="text-sm {conn.status === 'active'
											? 'text-green-600 dark:text-green-400'
											: 'text-muted-foreground'}">{conn.status}</span
									>
								</div>
								<Button variant="destructive" size="sm" onclick={() => deleteConnection(conn)}>
									Disconnect
								</Button>
							</div>

							<div class="flex flex-wrap gap-x-5 gap-y-2">
								{#each conn.permissions as perm (perm.capability)}
									<Label class="text-sm font-normal">
										<Checkbox
											checked={perm.allowed}
											onCheckedChange={(checked) =>
												togglePermission(conn, perm.capability, checked === true)}
										/>
										{CAPABILITY_LABELS[perm.capability] ?? perm.capability}
									</Label>
								{/each}
							</div>
						</Card.Content>
					</Card.Root>
				</li>
			{/each}
		</ul>
	{/if}
</main>
