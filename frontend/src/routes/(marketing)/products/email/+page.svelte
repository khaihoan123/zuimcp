<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import CheckIcon from '@lucide/svelte/icons/check';
	import { Button } from '$lib/components/ui/button';

	const providers = [
		{ name: 'IMAP', status: 'Available', body: 'Any standards-compliant mailbox, with an app password.' },
		{ name: 'Gmail', status: 'In progress', body: 'Native Gmail API via OAuth — no app passwords needed.' },
		{ name: 'Outlook', status: 'In progress', body: 'Native Microsoft Graph, including Microsoft 365 accounts.' }
	];

	const capabilities = [
		{ label: 'Read emails', body: 'Fetch and read messages from any folder.' },
		{ label: 'Search emails', body: 'Query by sender, subject, date or full text.' },
		{ label: 'Mark read / unread', body: 'Change message state — off by default.' },
		{ label: 'Create drafts', body: 'Compose drafts for you to review and send yourself.' }
	];
</script>

<svelte:head>
	<title>Email — controlled mailbox access for AI agents</title>
	<meta
		name="description"
		content="Connect IMAP, Gmail or Outlook to your AI agent and choose exactly which mailbox capabilities it gets."
	/>
</svelte:head>

<section class="mx-auto w-full max-w-4xl px-6 py-20">
	<p class="text-sm font-medium text-primary">Email</p>
	<h1 class="mt-3 text-4xl font-semibold tracking-tight">
		Your mailbox, with a permission dial
	</h1>
	<p class="mt-5 max-w-2xl text-lg text-muted-foreground">
		Connect a mailbox once, then decide exactly what your AI agent may do with it. Messages are read
		live at request time — we never copy your mail into our database.
	</p>
	<div class="mt-8">
		<Button href="/signup" size="lg">
			Connect a mailbox
			<ArrowRightIcon />
		</Button>
	</div>

	<h2 class="mt-20 text-2xl font-semibold tracking-tight">Providers</h2>
	<div class="mt-6 grid gap-4 sm:grid-cols-3">
		{#each providers as provider (provider.name)}
			<div class="rounded-xl bg-card p-5 ring-1 ring-foreground/10">
				<div class="flex items-center gap-2">
					<h3 class="font-semibold">{provider.name}</h3>
					<span
						class="rounded-full px-1.5 py-0.5 text-[10px] font-medium tracking-wide uppercase {provider.status ===
						'Available'
							? 'bg-primary/10 text-primary'
							: 'bg-muted text-muted-foreground'}"
					>
						{provider.status}
					</span>
				</div>
				<p class="mt-2 text-sm text-muted-foreground">{provider.body}</p>
			</div>
		{/each}
	</div>

	<h2 class="mt-16 text-2xl font-semibold tracking-tight">Capabilities you control</h2>
	<p class="mt-2 text-muted-foreground">
		Each one is an independent toggle, per connected account. Everything starts off.
	</p>
	<ul class="mt-6 grid gap-4 sm:grid-cols-2">
		{#each capabilities as capability (capability.label)}
			<li class="flex gap-3 rounded-xl bg-card p-4 ring-1 ring-foreground/10">
				<span class="mt-0.5 grid size-5 shrink-0 place-items-center rounded-full bg-primary/10 text-primary">
					<CheckIcon class="size-3" />
				</span>
				<div>
					<p class="font-medium">{capability.label}</p>
					<p class="mt-1 text-sm text-muted-foreground">{capability.body}</p>
				</div>
			</li>
		{/each}
	</ul>
</section>
