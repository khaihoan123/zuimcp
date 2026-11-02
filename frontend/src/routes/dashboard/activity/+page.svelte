<script lang="ts">
	import { onMount } from 'svelte';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';
	import { auditStatusLabel, capabilityLabel } from '$lib/config';

	type AuditLogEntry = {
		id: number;
		tool_name: string;
		capability: string | null;
		status: string;
		detail: string | null;
		created_at: string;
	};

	let entries = $state<AuditLogEntry[]>([]);
	let loading = $state(true);
	let loadError = $state('');

	async function loadEntries() {
		loading = true;
		loadError = '';
		try {
			const res = await fetch('/api/audit-log');
			if (!res.ok) throw new Error('Failed to load activity');
			entries = await res.json();
		} catch (e) {
			loadError = e instanceof Error ? e.message : 'Something went wrong';
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		loadEntries();
	});

	function formatTimestamp(iso: string): string {
		return new Date(iso).toLocaleString();
	}
</script>

<svelte:head>
	<title>Activity</title>
</svelte:head>

{#snippet statusBadge(status: string)}
	{#if status === 'success'}
		<Badge variant="outline" class="text-green-600 dark:text-green-400">
			{auditStatusLabel(status)}
		</Badge>
	{:else if status === 'rate_limited'}
		<Badge variant="outline" class="text-amber-600 dark:text-amber-400">
			{auditStatusLabel(status)}
		</Badge>
	{:else}
		<Badge variant="destructive">{auditStatusLabel(status)}</Badge>
	{/if}
{/snippet}

<div class="mx-auto max-w-4xl">
	<div>
		<h1 class="text-2xl font-semibold">Activity</h1>
		<p class="mt-2 text-sm text-muted-foreground">
			What your agents have been doing with your connected accounts.
		</p>
	</div>

	{#if loading}
		<p class="mt-8 text-sm text-muted-foreground">Loading…</p>
	{:else if loadError}
		<p class="mt-8 text-sm text-destructive">{loadError}</p>
	{:else if entries.length === 0}
		<Card.Root class="mt-8">
			<Card.Content class="flex flex-col items-center gap-2 py-12 text-center">
				<p class="font-medium">No activity yet</p>
				<p class="max-w-sm text-sm text-muted-foreground">
					Tool calls your agent makes against your connected accounts will show up here.
				</p>
			</Card.Content>
		</Card.Root>
	{:else}
		<Card.Root class="mt-8">
			<Card.Content class="flex flex-col gap-0 divide-y divide-border p-0">
				{#each entries as entry (entry.id)}
					<div class="flex flex-col gap-1 px-5 py-4">
						<div class="flex items-center justify-between gap-3">
							<div class="flex items-center gap-2">
								<span class="font-medium">{entry.tool_name}</span>
								{#if entry.capability}
									<span class="text-sm text-muted-foreground">
										{capabilityLabel(entry.capability)}
									</span>
								{/if}
							</div>
							{@render statusBadge(entry.status)}
						</div>
						<div class="flex items-center justify-between gap-3 text-sm text-muted-foreground">
							<span>{formatTimestamp(entry.created_at)}</span>
							{#if entry.detail}
								<span class="max-w-md truncate" title={entry.detail}>{entry.detail}</span>
							{/if}
						</div>
					</div>
				{/each}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
