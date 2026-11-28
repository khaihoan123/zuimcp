<script lang="ts">
	import CheckIcon from '@lucide/svelte/icons/check';
	import CopyIcon from '@lucide/svelte/icons/copy';

	/** The ONE MCP endpoint (MCP_RESOURCE_INDICATOR), shown in the dashboard header. */
	let { url }: { url: string } = $props();

	let copied = $state(false);
	let copyFailed = $state(false);

	async function copy() {
		copyFailed = false;
		try {
			await navigator.clipboard.writeText(url);
			copied = true;
			setTimeout(() => (copied = false), 2000);
		} catch {
			// Clipboard is blocked on insecure origins and without permission:
			// say so, and leave the URL selectable.
			copyFailed = true;
			setTimeout(() => (copyFailed = false), 4000);
		}
	}
</script>

<div class="relative flex min-w-0 items-center">
	<button
		type="button"
		onclick={copy}
		title="One URL for all your accounts. Add it as a custom connector in Claude or any MCP client. Sign-in is OAuth, so there's no API key."
		class="flex min-w-0 items-center gap-2 rounded-lg border border-border bg-muted/50 px-2.5 py-1.5 text-sm transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none"
		aria-label="Copy your MCP endpoint URL"
	>
		<span class="shrink-0 font-medium text-muted-foreground">
			<span class="hidden sm:inline">MCP endpoint</span>
			<span class="sm:hidden">Copy MCP URL</span>
		</span>
		<code class="hidden min-w-0 truncate font-mono text-xs select-all md:block md:max-w-72 lg:max-w-md">
			{url}
		</code>
		{#if copied}
			<CheckIcon class="size-4 shrink-0 text-green-600 dark:text-green-400" />
		{:else}
			<CopyIcon class="size-4 shrink-0 text-muted-foreground" />
		{/if}
	</button>
	<!-- Floats under the button, so the header never shifts. -->
	{#if copyFailed}
		<span
			class="absolute top-full right-0 z-10 mt-1 rounded-md border border-border bg-popover px-2 py-1 text-xs whitespace-nowrap text-destructive shadow-sm"
		>
			Copy blocked: select the URL and copy it.
		</span>
	{/if}
</div>
