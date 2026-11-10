<script lang="ts">
	import { onMount } from 'svelte';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import ConnectorIcon from '$lib/components/connector-icon.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import * as Dialog from '$lib/components/ui/dialog';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { serviceCategories, type Connector } from '$lib/config';

	type Connection = {
		id: number;
		service_type: string;
		display_name: string;
		status: string;
		permissions: { capability: string; allowed: boolean }[];
	};

	let { data } = $props();

	let connections = $state<Connection[]>([]);
	let loadError = $state('');

	/** The connector whose dialog is open, or null. */
	let connecting = $state<Connector | null>(null);
	let dialogOpen = $state(false);
	let addSubmitting = $state(false);
	let addError = $state('');
	let addedName = $state('');
	let form = $state({
		display_name: '',
		host: '',
		port: 993,
		email: '',
		password: '',
		smtp_host: '',
		smtp_port: 587
	});
	/** True until the user edits smtp_host directly, so it tracks host for
	 * providers that use the same base domain (mail.example.com for both). */
	let smtpHostTouched = false;

	/** Odoo 8 to 19 — the backend detects the version and picks the API. */
	let odooForm = $state({ display_name: '', url: '', database: '', login: '', api_key: '' });

	let copied = $state(false);
	let copyFailed = $state(false);

	function countFor(serviceType: string) {
		return connections.filter((c) => c.service_type === serviceType).length;
	}

	onMount(async () => {
		try {
			const res = await fetch('/api/connections');
			if (!res.ok) throw new Error('Failed to load connections');
			connections = await res.json();
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Something went wrong';
		}
	});

	async function copyEndpoint() {
		copyFailed = false;
		try {
			await navigator.clipboard.writeText(data.mcpEndpoint);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			// Clipboard is blocked on insecure origins and without permission —
			// tell the user rather than silently doing nothing.
			copyFailed = true;
		}
	}

	function openConnect(connector: Connector) {
		connecting = connector;
		addError = '';
		addedName = '';
		form = {
			display_name: '',
			host: '',
			port: 993,
			email: '',
			password: '',
			smtp_host: '',
			smtp_port: 587
		};
		smtpHostTouched = false;
		odooForm = { display_name: '', url: '', database: '', login: '', api_key: '' };
		dialogOpen = true;
	}

	function onHostInput(value: string) {
		form.host = value;
		if (!smtpHostTouched) form.smtp_host = value;
	}

	function onSmtpHostInput(value: string) {
		smtpHostTouched = true;
		form.smtp_host = value;
	}

	async function addConnection(e: SubmitEvent) {
		e.preventDefault();
		addSubmitting = true;
		addError = '';
		try {
			const isOdoo = connecting?.id === 'odoo';
			const res = await fetch(isOdoo ? '/api/connections/odoo' : '/api/connections/imap', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(isOdoo ? odooForm : form)
			});
			const body = await res.json();
			if (!res.ok) throw new Error(body.detail ?? 'Could not connect');
			connections = [...connections, body];
			addedName = body.display_name;
			dialogOpen = false;
		} catch (e) {
			addError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			addSubmitting = false;
		}
	}
</script>

<svelte:head>
	<title>Connections</title>
</svelte:head>

<div>
	<h1 class="text-2xl font-semibold">Connections</h1>
	<p class="mt-2 text-sm text-muted-foreground">
		Pick a connector to link an account. You can connect several accounts per service.
	</p>

	<!-- One endpoint for everything: the agent sees the tools each account allows. -->
	<Card.Root class="mt-6" size="sm">
		<Card.Content class="flex flex-col gap-2">
			<div class="flex items-center gap-2">
				<p class="text-sm font-medium">Your MCP endpoint</p>
				<Badge variant="secondary">Live</Badge>
			</div>
			<div class="flex items-center gap-2">
				<code
					class="min-w-0 flex-1 truncate rounded-lg border border-border bg-muted/50 px-3 py-2 font-mono text-sm"
				>
					{data.mcpEndpoint}
				</code>
				<Button
					variant="outline"
					size="icon"
					onclick={copyEndpoint}
					aria-label="Copy MCP endpoint URL"
				>
					{#if copied}
						<CheckIcon />
					{:else}
						<CopyIcon />
					{/if}
				</Button>
			</div>
			{#if copyFailed}
				<p class="text-sm text-destructive">
					Couldn't access the clipboard — select the URL and copy it manually.
				</p>
			{/if}
			<p class="text-sm text-muted-foreground">
				One URL for all your accounts. Add it as a remote MCP server in your agent's config — auth
				is OAuth, so there's no API key. Your agent only sees the actions you allow on each account.
			</p>
		</Card.Content>
	</Card.Root>

	{#if addedName}
		<div
			class="mt-6 flex items-center gap-2 rounded-lg border border-green-600/30 bg-green-600/10 px-4 py-3 text-sm"
		>
			<CheckIcon class="size-4 text-green-600 dark:text-green-400" />
			<span>
				Connected <strong>{addedName}</strong>. No capabilities are granted yet —
				<a class="text-primary underline-offset-4 hover:underline" href="/dashboard/accounts">
					choose what your agent may do
				</a>.
			</span>
		</div>
	{/if}

	{#if loadError}
		<p class="mt-6 text-sm text-destructive">{loadError}</p>
	{/if}

	{#each serviceCategories as category (category.id)}
		<section class="mt-10">
			<div class="mb-1 flex items-baseline gap-3">
				<h2 class="text-base font-semibold">{category.label}</h2>
				<span class="text-sm text-muted-foreground">{category.description}</span>
			</div>


			<div class="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
				{#each category.connectors as connector (connector.id)}
					{@const count = countFor(connector.id)}
					{#if connector.availability === 'available'}
						<button
							class="group flex flex-col gap-3 rounded-xl bg-card p-5 text-left ring-1 ring-foreground/10 transition-all hover:ring-2 hover:ring-primary/40 focus-visible:ring-2 focus-visible:ring-ring"
							onclick={() => openConnect(connector)}
						>
							<div class="flex items-start justify-between gap-2">
								<ConnectorIcon {connector} />
								{#if count > 0}
									<Badge variant="secondary">{count} connected</Badge>
								{/if}
							</div>
							<div>
								<p class="font-medium">{connector.label}</p>
								<p class="mt-1 text-sm text-muted-foreground">{connector.description}</p>
							</div>
							<span class="inline-flex items-center gap-1 text-sm font-medium text-primary">
								<PlusIcon class="size-4" />
								Connect
							</span>
						</button>
					{:else}
						<div
							class="flex flex-col gap-3 rounded-xl border border-dashed border-border bg-card/40 p-5"
						>
							<div class="flex items-start justify-between gap-2">
								<ConnectorIcon {connector} class="opacity-60" />
								<Badge variant="outline">Coming soon</Badge>
							</div>
							<div>
								<p class="font-medium text-muted-foreground">{connector.label}</p>
								<p class="mt-1 text-sm text-muted-foreground">{connector.description}</p>
							</div>
						</div>
					{/if}
				{/each}
			</div>
		</section>
	{/each}
</div>

<!-- Connect dialog: keeps the catalogue in place behind it. -->
<Dialog.Root bind:open={dialogOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title class="flex items-center gap-2">
				{#if connecting}
					<ConnectorIcon connector={connecting} class="size-8" />
				{/if}
				Connect {connecting?.label ?? ''}
			</Dialog.Title>
			<Dialog.Description>
				We verify the credentials before saving them, and store them encrypted.
			</Dialog.Description>
		</Dialog.Header>

		<form class="flex flex-col gap-3" onsubmit={addConnection}>
			{#if connecting?.id === 'odoo'}
				<div class="flex flex-col gap-1.5">
					<Label for="odoo_display_name">Display name</Label>
					<Input
						id="odoo_display_name"
						type="text"
						bind:value={odooForm.display_name}
						placeholder="Live Odoo"
						required
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="odoo_url">Odoo URL</Label>
					<Input
						id="odoo_url"
						type="url"
						bind:value={odooForm.url}
						placeholder="https://mycompany.odoo.com"
						required
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="odoo_database">Database</Label>
					<Input
						id="odoo_database"
						type="text"
						bind:value={odooForm.database}
						placeholder="mycompany"
						required
						autocomplete="off"
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="odoo_login">Login</Label>
					<Input
						id="odoo_login"
						type="text"
						bind:value={odooForm.login}
						placeholder="you@example.com"
						required
						autocomplete="off"
					/>
				</div>
				<div class="flex flex-col gap-1.5">
					<Label for="odoo_api_key">API key (or password on Odoo 13 and older)</Label>
					<Input
						id="odoo_api_key"
						type="password"
						bind:value={odooForm.api_key}
						required
						autocomplete="off"
					/>
					<p class="text-xs text-muted-foreground">
						In Odoo: your avatar → My Profile (or Preferences) → Account Security → New API Key.
						Pick a long <strong>Duration</strong> — Odoo preselects 1 day, and the connection stops
						when the key expires. Administrators can choose Persistent Key; other users up to 3 months.
						Odoo 13 and older have no API keys: use the password of an Odoo user made just for
						your agent. Your agent gets that Odoo user's access rights, limited further by what you allow here.
					</p>
				</div>
			{:else}
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
							value={form.host}
							oninput={(e) => onHostInput(e.currentTarget.value)}
							placeholder="mail.example.com"
							required
						/>
					</div>
					<div class="flex flex-col gap-1.5">
						<Label for="port">Port</Label>
						<Input id="port" type="number" bind:value={form.port} required class="w-24" />
					</div>
				</div>
				<div class="flex gap-3">
					<div class="flex flex-1 flex-col gap-1.5">
						<Label for="smtp_host">SMTP host</Label>
						<Input
							id="smtp_host"
							type="text"
							value={form.smtp_host}
							oninput={(e) => onSmtpHostInput(e.currentTarget.value)}
							placeholder="mail.example.com"
							required
						/>
					</div>
					<div class="flex flex-col gap-1.5">
						<Label for="smtp_port">Port</Label>
						<Input id="smtp_port" type="number" bind:value={form.smtp_port} required class="w-24" />
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
			{/if}
			{#if addError}
				<p class="text-sm text-destructive">{addError}</p>
			{/if}
			<div class="mt-1 flex justify-end gap-2">
				<Dialog.Close>
					{#snippet child({ props })}
						<Button {...props} type="button" variant="outline">Cancel</Button>
					{/snippet}
				</Dialog.Close>
				<Button type="submit" disabled={addSubmitting}>
					{addSubmitting ? 'Connecting…' : 'Connect'}
				</Button>
			</div>
		</form>
	</Dialog.Content>
</Dialog.Root>
