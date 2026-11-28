<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { replaceState } from '$app/navigation';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ConnectorIcon from '$lib/components/connector-icon.svelte';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';
	import * as Card from '$lib/components/ui/card';
	import { auditStatusLabel, capabilityLabel, connectorFor } from '$lib/config';

	type Actor = 'agent' | 'user' | 'system';
	type Entry = {
		id: number;
		actor: Actor;
		/** Dashboard/system changes, e.g. 'permission.on'. */
		action: string | null;
		/** Agent calls. */
		tool_name: string | null;
		capability: string | null;
		/** Link to the account; null once it is disconnected. */
		connection_id: number | null;
		/** The account as it was at the time. */
		account_name: string | null;
		service_type: string | null;
		account_target: string | null;
		/** The account was filled in afterwards, not recorded at the time. */
		inferred: boolean;
		status: string;
		detail: string | null;
		created_at: string;
	};
	type Account = { id: number; display_name: string; service_type: string };

	const PAGE_SIZE = 50;
	/** Seconds between automatic updates while the page is visible. */
	const LIVE_INTERVAL = 15;

	let entries = $state<Entry[]>([]);
	let loading = $state(true);
	let loadingMore = $state(false);
	let hasMore = $state(false);
	let loadError = $state('');
	let accounts = $state<Account[]>([]);

	let who = $state<'all' | 'agent' | 'user'>('all');
	let errorsOnly = $state(false);
	/** Only entries about this account (?account=<id>), or null for all. */
	let accountFilter = $state<number | null>(null);

	let accountsById = $derived(new Map(accounts.map((a) => [a.id, a])));
	/** The filter is only worth its space with several accounts (or when a link set it). */
	let showAccountFilter = $derived(accounts.length >= 2 || accountFilter !== null);

	function query(beforeId?: number): string {
		const params = new URLSearchParams({ limit: String(PAGE_SIZE) });
		if (beforeId !== undefined) params.set('before_id', String(beforeId));
		if (accountFilter !== null) params.set('connection_id', String(accountFilter));
		if (who !== 'all') params.set('actor', who);
		if (errorsOnly) params.set('errors_only', 'true');
		return `/api/audit-log?${params}`;
	}

	async function fetchPage(beforeId?: number): Promise<Entry[]> {
		const res = await fetch(query(beforeId));
		if (!res.ok) throw new Error('Failed to load activity');
		return res.json();
	}

	/** First page, for the current filters. */
	async function reload() {
		loadError = '';
		try {
			const page = await fetchPage();
			entries = page;
			hasMore = page.length === PAGE_SIZE;
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			loading = false;
		}
	}

	async function loadMore() {
		const last = entries.at(-1);
		if (!last) return;
		loadingMore = true;
		try {
			const page = await fetchPage(last.id);
			entries = [...entries, ...page];
			hasMore = page.length === PAGE_SIZE;
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			loadingMore = false;
		}
	}

	/** New entries held back while the user is scrolled down reading, so rows
	 * don't jump under them. Shown with the "N new" button. */
	let pending = $state<Entry[]>([]);

	/** Adds entries newer than the newest known, without disturbing older pages. */
	async function pollNew() {
		if (document.hidden || loading) return;
		try {
			const page = await fetchPage();
			const newest = pending[0]?.id ?? entries[0]?.id ?? 0;
			const fresh = page.filter((e) => e.id > newest);
			if (!fresh.length) return;
			if (window.scrollY > 120) pending = [...fresh, ...pending];
			else entries = [...fresh, ...entries];
		} catch {
			// Try again on the next tick.
		}
	}

	function showPending() {
		entries = [...pending, ...entries];
		pending = [];
		window.scrollTo({ top: 0, behavior: 'smooth' });
	}

	async function loadAccounts() {
		try {
			const res = await fetch('/api/connections');
			if (res.ok) accounts = await res.json();
		} catch {
			// Only used for current names and the filter.
		}
	}

	/** Filters live in the URL, so a link from an account card opens filtered. */
	function applyFilters() {
		const url = new URL(window.location.href);
		if (accountFilter === null) url.searchParams.delete('account');
		else url.searchParams.set('account', String(accountFilter));
		replaceState(url, {});
		pending = [];
		loading = true;
		reload();
	}

	let timer: ReturnType<typeof setInterval> | undefined;

	onMount(() => {
		const id = Number(new URL(window.location.href).searchParams.get('account'));
		accountFilter = Number.isInteger(id) && id > 0 ? id : null;
		reload();
		loadAccounts();
		timer = setInterval(pollNew, LIVE_INTERVAL * 1000);
		document.addEventListener('visibilitychange', pollNew);
	});

	onDestroy(() => {
		clearInterval(timer);
		if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', pollNew);
	});

	const dateFormat = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
	const timeFormat = new Intl.DateTimeFormat(undefined, {
		hour: '2-digit',
		minute: '2-digit',
		second: '2-digit'
	});

	const actionLabels: Record<string, string> = {
		'account.connected': 'Connected the account',
		'account.renamed': 'Renamed',
		'account.login_updated': 'Updated the login',
		'account.login_refused': 'Login refused',
		'account.paused': 'Paused',
		'account.resumed': 'Resumed',
		'account.disconnected': 'Disconnected'
	};

	function actionLabel(entry: Entry): string {
		if (entry.action === 'permission.on') return `Allowed: ${capabilityLabel(entry.capability ?? '')}`;
		if (entry.action === 'permission.off') return `Turned off: ${capabilityLabel(entry.capability ?? '')}`;
		if (entry.action) return actionLabels[entry.action] ?? entry.action;
		return entry.capability ? capabilityLabel(entry.capability) : 'List accounts';
	}

	/** Service from the tool name, for agent calls that never reached an account. */
	function serviceOfTool(tool: string | null): string | null {
		const prefix = tool?.split('_')[0];
		return prefix === 'email' ? 'Email' : prefix === 'odoo' ? 'Odoo' : null;
	}

	/** The parts of an account's target (core.accounts.connection_identity):
	 * "login on host (database db, Odoo 19.0)" for Odoo, the address for email. */
	type Target = { login?: string; host?: string; database?: string; version?: string; address?: string };

	function parseTarget(target: string | null): Target | null {
		if (!target) return null;
		const odoo = target.match(/^(.*) on (\S+) \(database (.+?)(?:, Odoo (.+))?\)$/);
		if (odoo) return { login: odoo[1], host: odoo[2], database: odoo[3], version: odoo[4] };
		return { address: target };
	}

	/** One short line for the table: the Odoo server or the mailbox. */
	function shortTarget(entry: Entry): string | null {
		const t = parseTarget(entry.account_target);
		return t?.host ?? t?.address ?? null;
	}

	/** Older entries still carry FastMCP's prefix; the Action column names the tool. */
	function detailText(entry: Entry): string | null {
		return entry.detail?.replace(/^Error calling tool '[^']*': /, '') ?? null;
	}

	/** The row whose details are open. */
	let expandedId = $state<number | null>(null);

	function toggle(id: number) {
		expandedId = expandedId === id ? null : id;
	}

	const fullTime = new Intl.DateTimeFormat(undefined, {
		dateStyle: 'medium',
		timeStyle: 'long'
	});

	const segment = (active: boolean) =>
		`rounded-md px-3 py-1 ${active ? 'bg-muted font-medium' : 'text-muted-foreground hover:text-foreground'}`;
</script>

<svelte:head>
	<title>Activity</title>
</svelte:head>

{#snippet statusCell(entry: Entry)}
	{#if entry.action === 'account.login_refused'}
		<Badge variant="outline" class="text-amber-600 dark:text-amber-400">Warning</Badge>
	{:else if entry.actor !== 'agent'}
		<span class="text-muted-foreground">—</span>
	{:else if entry.status === 'success'}
		<Badge variant="outline" class="text-green-600 dark:text-green-400">
			{auditStatusLabel(entry.status)}
		</Badge>
	{:else if entry.status === 'rate_limited'}
		<Badge variant="outline" class="text-amber-600 dark:text-amber-400">
			{auditStatusLabel(entry.status)}
		</Badge>
	{:else}
		<Badge variant="destructive">{auditStatusLabel(entry.status)}</Badge>
	{/if}
{/snippet}

{#snippet accountCell(entry: Entry)}
	{@const connector = entry.service_type ? connectorFor(entry.service_type) : undefined}
	{@const current = entry.connection_id !== null ? accountsById.get(entry.connection_id) : undefined}
	{#if entry.account_name}
		<!-- Two short lines; the full target is in the expanded row. -->
		<div class="flex min-w-0 items-center gap-2.5">
			{#if connector}
				<ConnectorIcon {connector} class="size-7 [&_svg]:size-4" />
			{/if}
			<div class="min-w-0">
				<div class="truncate font-medium">
					{#if entry.connection_id !== null}
						<!-- The name as it was; the link follows the account itself. -->
						<a
							href={`/dashboard/accounts#account-${entry.connection_id}`}
							class="group/link inline-flex max-w-full items-center gap-1 decoration-primary/60 underline-offset-2 hover:text-primary hover:underline"
							title={current && current.display_name !== entry.account_name
								? `Open this account (now called "${current.display_name}")`
								: 'Open this account'}
						>
							<span class="truncate">{entry.account_name}</span>
							<ArrowUpRightIcon
								class="size-3.5 shrink-0 opacity-0 transition-opacity group-hover/link:opacity-100"
							/>
						</a>
					{:else}
						{entry.account_name}
						<span class="font-normal text-muted-foreground">(disconnected)</span>
					{/if}
				</div>
				<div class="truncate text-xs text-muted-foreground">
					{shortTarget(entry) ?? connector?.label ?? entry.service_type}
					{#if entry.inferred}
						<span class="mx-1">·</span><span class="italic">filled in later</span>
					{/if}
				</div>
			</div>
		</div>
	{:else}
		<div class="whitespace-nowrap text-muted-foreground">
			{serviceOfTool(entry.tool_name) ?? '—'}
			{#if serviceOfTool(entry.tool_name)}
				<div class="text-xs">account not recorded</div>
			{/if}
		</div>
	{/if}
{/snippet}

{#snippet field(label: string, value: string | null | undefined, mono = false)}
	{#if value}
		<div class="min-w-0">
			<dt class="text-xs text-muted-foreground">{label}</dt>
			<dd class="mt-0.5 break-words {mono ? 'font-mono text-xs' : ''}">{value}</dd>
		</div>
	{/if}
{/snippet}

{#snippet details(entry: Entry, created: Date, detail: string | null)}
	{@const target = parseTarget(entry.account_target)}
	{@const connector = entry.service_type ? connectorFor(entry.service_type) : undefined}
	{@const current = entry.connection_id !== null ? accountsById.get(entry.connection_id) : undefined}
	<!-- Two groups: what happened, and the account it happened on (as it was then). -->
	<div
		class="grid gap-6 rounded-lg border border-border bg-card p-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
	>
		<section class="min-w-0">
			<h3 class="text-xs font-medium tracking-wide text-muted-foreground uppercase">What happened</h3>
			<dl class="mt-3 grid gap-3 sm:grid-cols-2">
				{@render field('When', fullTime.format(created))}
				{@render field(entry.tool_name ? `Action · ${entry.tool_name}` : 'Action', actionLabel(entry))}
			</dl>
			{#if detail}
				<div class="mt-3">
					<p class="text-xs text-muted-foreground">Details</p>
					<p class="mt-0.5 whitespace-pre-wrap">{detail}</p>
				</div>
			{/if}
		</section>

		{#if entry.account_name}
			<section class="min-w-0 lg:border-l lg:border-border lg:pl-6">
				<h3 class="text-xs font-medium tracking-wide text-muted-foreground uppercase">
					Account at the time
				</h3>
				<dl class="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
					{@render field('Name', entry.account_name)}
					{@render field('Connector', connector?.label ?? entry.service_type)}
					{@render field('Mailbox', target?.address)}
					{@render field('Odoo server', target?.host)}
					{@render field('Database', target?.database)}
					{@render field('Login', target?.login)}
					{@render field('Odoo version', target?.version)}
				</dl>
				{@render accountLinks(entry, current, connector?.label)}
			</section>
		{/if}
	</div>
{/snippet}

{#snippet accountLinks(entry: Entry, current: Account | undefined, connectorLabel: string | undefined)}
	{#if entry.account_name}
			<div class="mt-4 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border pt-3 text-sm">
				{#if entry.connection_id !== null}
					<a
						class="font-medium text-primary underline-offset-4 hover:underline"
						href={`/dashboard/accounts#account-${entry.connection_id}`}
					>
						Open account{current ? ` "${current.display_name}"` : ''}
					</a>
					{#if current && current.display_name !== entry.account_name}
						<span class="text-muted-foreground">Renamed since: it was "{entry.account_name}".</span>
					{/if}
				{:else}
					<span class="text-muted-foreground">This account has been disconnected.</span>
				{/if}
				{#if entry.inferred}
					<span class="text-muted-foreground">
						The account was filled in later: this entry is from before ZuiMCP recorded accounts, and it
						was your only {connectorLabel ?? ''} account at the time.
					</span>
				{/if}
			</div>
	{/if}
{/snippet}

<div>
	<div class="flex flex-wrap items-end justify-between gap-4">
		<div>
			<h1 class="text-2xl font-semibold">Activity</h1>
			<p class="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
				What your AI agents did, and every change to your accounts. Message contents are never
				logged.
			</p>
		</div>
		<div class="flex flex-wrap items-center gap-2">
			{#if showAccountFilter}
				<select
					class="h-8 rounded-lg border border-border bg-card px-2.5 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
					aria-label="Filter by account"
					value={accountFilter === null ? '' : String(accountFilter)}
					onchange={(e) => {
						accountFilter = e.currentTarget.value ? Number(e.currentTarget.value) : null;
						applyFilters();
					}}
				>
					<option value="">All accounts</option>
					{#each accounts as account (account.id)}
						<option value={String(account.id)}>
							{account.display_name} ({connectorFor(account.service_type)?.label ??
								account.service_type})
						</option>
					{/each}
					{#if accountFilter !== null && !accountsById.has(accountFilter)}
						<option value={String(accountFilter)}>Disconnected account</option>
					{/if}
				</select>
			{/if}
			<div class="flex rounded-lg border border-border p-0.5 text-sm" role="group" aria-label="Who">
				{#each [['all', 'All'], ['agent', 'AI calls'], ['user', 'Account changes']] as [value, label] (value)}
					<button
						type="button"
						class={segment(who === value)}
						aria-pressed={who === value}
						onclick={() => {
							who = value as typeof who;
							applyFilters();
						}}
					>
						{label}
					</button>
				{/each}
			</div>
			<button
				type="button"
				class="rounded-lg border border-border p-0.5 text-sm"
				aria-pressed={errorsOnly}
				onclick={() => {
					errorsOnly = !errorsOnly;
					applyFilters();
				}}
			>
				<span class={segment(errorsOnly)}>Errors only</span>
			</button>
			<span
				class="flex items-center gap-1.5 text-xs text-muted-foreground"
				title="New activity appears automatically while this page is open."
			>
				<span class="size-1.5 animate-pulse rounded-full bg-green-500"></span>
				Live
			</span>
		</div>
	</div>

	{#if pending.length > 0}
		<div class="pointer-events-none fixed inset-x-0 top-20 z-20 flex justify-center">
			<Button class="pointer-events-auto shadow-lg" size="sm" onclick={showPending}>
				{pending.length} new {pending.length === 1 ? 'entry' : 'entries'} — show
			</Button>
		</div>
	{/if}

	{#if loading}
		<p class="mt-8 text-sm text-muted-foreground">Loading…</p>
	{:else if loadError && entries.length === 0}
		<p class="mt-8 text-sm text-destructive">{loadError}</p>
	{:else if entries.length === 0}
		<Card.Root class="mt-6">
			<Card.Content class="flex flex-col items-center gap-2 py-12 text-center">
				<p class="font-medium">
					{errorsOnly ? 'No errors' : who !== 'all' || accountFilter !== null ? 'Nothing here yet' : 'No activity yet'}
				</p>
				<p class="max-w-sm text-sm text-muted-foreground">
					Your agent's tool calls and your account changes will show up here as they happen.
				</p>
			</Card.Content>
		</Card.Root>
	{:else}
		<div class="mt-6 overflow-x-auto rounded-xl ring-1 ring-foreground/10">
			<!-- Fixed widths: each column gets what it needs, Details gets the rest. -->
			<table class="w-full min-w-[64rem] table-fixed border-collapse bg-card text-sm">
				<colgroup>
					<col class="w-12" />
					<col class="w-36" />
					<col class="w-72" />
					<col class="w-52" />
					<col class="w-28" />
					<col />
				</colgroup>
				<thead>
					<tr
						class="border-b border-border text-left text-xs font-medium tracking-wide text-muted-foreground uppercase"
					>
						<th class="py-3 pl-4"><span class="sr-only">Details</span></th>
						<th class="px-4 py-3 font-medium">Time</th>
						<th class="px-4 py-3 font-medium">Account</th>
						<th class="px-4 py-3 font-medium">Action</th>
						<th class="px-4 py-3 font-medium">Status</th>
						<th class="px-4 py-3 font-medium">Details</th>
					</tr>
				</thead>
				<tbody class="divide-y divide-border">
					{#each entries as entry (entry.id)}
						{@const created = new Date(entry.created_at)}
						{@const open = expandedId === entry.id}
						{@const detail = detailText(entry)}
						<!-- Only the arrow opens the details, so the row's text can be
						     selected and copied. -->
						<tr class="align-middle hover:bg-muted/40 {open ? 'bg-muted/40' : ''}">
							<td class="py-3 pl-4">
								<button
									type="button"
									class="grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
									aria-expanded={open}
									aria-label={open ? 'Hide details' : 'Show details'}
									title={open ? 'Hide details' : 'Show details'}
									onclick={() => toggle(entry.id)}
								>
									<ChevronRightIcon class="size-4 transition-transform {open ? 'rotate-90' : ''}" />
								</button>
							</td>
							<td class="px-4 py-3 whitespace-nowrap">
								<div>{timeFormat.format(created)}</div>
								<div class="text-xs text-muted-foreground">{dateFormat.format(created)}</div>
							</td>
							<td class="px-4 py-3">{@render accountCell(entry)}</td>
							<td class="px-4 py-3">
								<div class="truncate font-medium">{actionLabel(entry)}</div>
								{#if entry.tool_name}
									<code class="block truncate text-xs text-muted-foreground">{entry.tool_name}</code>
								{/if}
							</td>
							<td class="px-4 py-3">{@render statusCell(entry)}</td>
							<td class="px-4 py-3">
								{#if detail}
									<!-- Two lines at most here; the full text is in the expanded row. -->
									<p
										class="line-clamp-2 {entry.actor === 'agent' && entry.status !== 'success'
											? ''
											: 'text-muted-foreground'}"
									>
										{detail}
									</p>
								{:else}
									<span class="text-muted-foreground">—</span>
								{/if}
							</td>
						</tr>
						{#if open}
							<tr class="bg-muted/20">
								<td></td>
								<td colspan="5" class="px-4 pt-1 pb-5">
									{@render details(entry, created, detail)}
								</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
		<div class="mt-4 flex justify-center">
			{#if hasMore}
				<Button variant="outline" size="sm" onclick={loadMore} disabled={loadingMore}>
					{loadingMore ? 'Loading…' : 'Load more'}
				</Button>
			{:else}
				<p class="text-xs text-muted-foreground">That's everything.</p>
			{/if}
		</div>
	{/if}
</div>
