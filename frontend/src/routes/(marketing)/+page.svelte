<script lang="ts">
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import CheckIcon from '@lucide/svelte/icons/check';
	import EyeOffIcon from '@lucide/svelte/icons/eye-off';
	import MailIcon from '@lucide/svelte/icons/mail';
	import PlugIcon from '@lucide/svelte/icons/plug';
	import ServerIcon from '@lucide/svelte/icons/server';
	import SlidersHorizontalIcon from '@lucide/svelte/icons/sliders-horizontal';
	import { Button } from '$lib/components/ui/button';
	import { SITE_NAME, products } from '$lib/config';

	let { data } = $props();

	const features = [
		{
			icon: SlidersHorizontalIcon,
			title: 'Per-capability permissions',
			body: 'Not all-or-nothing access. Grant reading but not sending, searching but not writing — toggle each capability independently, per connected account.'
		},
		{
			icon: ServerIcon,
			title: 'Native APIs, not scraping',
			body: 'Each service is integrated through its real API — Gmail, Microsoft Graph — with generic fallbacks where it makes sense. Faster and more reliable.'
		},
		{
			icon: EyeOffIcon,
			title: 'Nothing stored',
			body: 'Your data is read live at request time and passed straight through. We never copy it into our database — there is no archive to breach.'
		}
	];

	const steps = [
		{ title: 'Create an account', body: 'Sign up in seconds. No credit card, no sales call.' },
		{
			title: 'Connect a service',
			body: 'Start with a mailbox — IMAP today, Gmail and Outlook next. Credentials are encrypted at rest.'
		},
		{
			title: 'Choose what your agent can do',
			body: 'Flip on exactly the capabilities you want. Change your mind any time — it takes effect immediately.'
		}
	];

	const capabilities = [
		{ label: 'Read emails', on: true },
		{ label: 'Search emails', on: true },
		{ label: 'Mark read / unread', on: false },
		{ label: 'Create drafts', on: false }
	];
</script>

<svelte:head>
	<title>{SITE_NAME} — controlled access to your tools, for AI agents</title>
	<meta
		name="description"
		content="Connect your email, ERP and other business tools to any MCP-compatible AI agent — and control exactly what each one is allowed to do."
	/>
</svelte:head>

<!-- Hero -->
<section class="relative overflow-hidden">
	<div
		class="pointer-events-none absolute inset-x-0 -top-40 -z-10 h-96 bg-gradient-to-b from-primary/15 to-transparent blur-3xl"
	></div>
	<div
		class="mx-auto grid w-full max-w-6xl gap-12 px-6 py-20 lg:grid-cols-2 lg:items-center lg:py-28"
	>
		<div>
			<p
				class="mb-4 inline-flex items-center gap-2 rounded-full border border-border bg-card px-3 py-1 text-xs font-medium text-muted-foreground"
			>
				<PlugIcon class="size-3.5" />
				Works with any MCP-compatible agent
			</p>
			<h1 class="text-4xl leading-tight font-semibold tracking-tight sm:text-5xl">
				Connect your tools to AI — <span class="text-primary">on your terms</span>
			</h1>
			<p class="mt-5 max-w-lg text-lg text-muted-foreground">
				One place to connect the accounts your AI agent needs — email today, your ERP and more next
				— and decide exactly what it's allowed to do with each. Read but not send. Search but not
				write. You choose, per account.
			</p>
			<div class="mt-8 flex flex-wrap items-center gap-4">
				{#if data.user}
					<Button href="/dashboard" size="lg">
						Go to dashboard
						<ArrowRightIcon />
					</Button>
				{:else}
					<Button href="/signup" size="lg">
						Connect for free
						<ArrowRightIcon />
					</Button>
					<a class="text-sm font-medium text-muted-foreground hover:text-foreground" href="/login">
						or sign in
					</a>
				{/if}
			</div>
			<p class="mt-4 text-sm text-muted-foreground">
				No credit card required. Disconnect any time.
			</p>
		</div>

		<!-- Product visual: a mock of the real permission card -->
		<div class="relative">
			<div
				class="rounded-xl bg-card p-5 shadow-xl ring-1 ring-foreground/10 sm:p-6"
				aria-hidden="true"
			>
				<div class="flex items-center gap-2.5">
					<span
						class="rounded-full bg-muted px-2 py-0.5 text-xs tracking-wide text-muted-foreground uppercase"
					>
						gmail
					</span>
					<span class="font-semibold">Work inbox</span>
					<span class="text-sm text-green-600 dark:text-green-400">active</span>
				</div>
				<div class="mt-5 space-y-3">
					{#each capabilities as cap (cap.label)}
						<div class="flex items-center gap-3 text-sm">
							<span
								class="grid size-4 shrink-0 place-items-center rounded-[4px] border {cap.on
									? 'border-primary bg-primary text-primary-foreground'
									: 'border-input'}"
							>
								{#if cap.on}
									<CheckIcon class="size-3" />
								{/if}
							</span>
							<span class={cap.on ? 'text-foreground' : 'text-muted-foreground'}>{cap.label}</span>
						</div>
					{/each}
				</div>
				<div
					class="mt-5 flex items-center gap-2 border-t border-border pt-4 text-xs text-muted-foreground"
				>
					<MailIcon class="size-3.5" />
					Your agent can read and search — but cannot draft or change anything.
				</div>
			</div>
		</div>
	</div>
</section>

<!-- Services -->
<section class="border-t border-border/60">
	<div class="mx-auto w-full max-w-6xl px-6 py-20">
		<h2 class="text-center text-3xl font-semibold tracking-tight">One platform, many services</h2>
		<p class="mx-auto mt-3 max-w-xl text-center text-muted-foreground">
			Every service works the same way: connect the account, then pick its capabilities.
		</p>
		<div class="mx-auto mt-12 grid max-w-3xl gap-4 sm:grid-cols-2">
			{#each products as product (product.label)}
				{#if product.href}
					<a
						href={product.href}
						class="group rounded-xl bg-card p-6 ring-1 ring-foreground/10 transition-shadow hover:shadow-md"
					>
						<h3 class="font-semibold">{product.label}</h3>
						<p class="mt-2 text-sm text-muted-foreground">{product.description}</p>
						<span class="mt-4 inline-flex items-center gap-1 text-sm font-medium text-primary">
							Learn more
							<ArrowRightIcon class="size-4 transition-transform group-hover:translate-x-0.5" />
						</span>
					</a>
				{:else}
					<div class="rounded-xl border border-dashed border-border bg-card/40 p-6">
						<div class="flex items-center gap-2">
							<h3 class="font-semibold text-muted-foreground">{product.label}</h3>
							<span
								class="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-muted-foreground uppercase"
							>
								Coming soon
							</span>
						</div>
						<p class="mt-2 text-sm text-muted-foreground">{product.description}</p>
					</div>
				{/if}
			{/each}
		</div>
	</div>
</section>

<!-- Features -->
<section class="border-t border-border/60 bg-card/40">
	<div class="mx-auto w-full max-w-6xl px-6 py-20">
		<h2 class="text-center text-3xl font-semibold tracking-tight">Built for cautious people</h2>
		<p class="mx-auto mt-3 max-w-xl text-center text-muted-foreground">
			Handing an AI agent your business data should not mean handing over everything at once.
		</p>
		<div class="mt-12 grid gap-6 md:grid-cols-3">
			{#each features as feature (feature.title)}
				<div class="rounded-xl bg-card p-6 ring-1 ring-foreground/10">
					<span class="grid size-10 place-items-center rounded-lg bg-primary/10 text-primary">
						<feature.icon class="size-5" />
					</span>
					<h3 class="mt-4 font-semibold">{feature.title}</h3>
					<p class="mt-2 text-sm text-muted-foreground">{feature.body}</p>
				</div>
			{/each}
		</div>
	</div>
</section>

<!-- How it works -->
<section class="border-t border-border/60">
	<div class="mx-auto w-full max-w-6xl px-6 py-20">
		<h2 class="text-center text-3xl font-semibold tracking-tight">Three steps to connected</h2>
		<ol class="mt-12 grid gap-8 md:grid-cols-3">
			{#each steps as step, i (step.title)}
				<li>
					<span
						class="grid size-8 place-items-center rounded-full bg-primary text-sm font-semibold text-primary-foreground"
					>
						{i + 1}
					</span>
					<h3 class="mt-4 font-semibold">{step.title}</h3>
					<p class="mt-2 text-sm text-muted-foreground">{step.body}</p>
				</li>
			{/each}
		</ol>
	</div>
</section>

<!-- Final CTA -->
<section class="border-t border-border/60 bg-card/40">
	<div class="mx-auto w-full max-w-3xl px-6 py-20 text-center">
		<h2 class="text-3xl font-semibold tracking-tight">Ready to connect your first account?</h2>
		<p class="mt-3 text-muted-foreground">
			Set it up in a couple of minutes, and revoke access just as fast.
		</p>
		<div class="mt-8 flex justify-center">
			<Button href={data.user ? '/dashboard' : '/signup'} size="lg">
				{data.user ? 'Go to dashboard' : 'Connect for free'}
				<ArrowRightIcon />
			</Button>
		</div>
	</div>
</section>
