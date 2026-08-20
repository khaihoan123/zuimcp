<script lang="ts">
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import InboxIcon from '@lucide/svelte/icons/inbox';
	import MailIcon from '@lucide/svelte/icons/mail';
	import ServerIcon from '@lucide/svelte/icons/server';
	import type { Connector } from '$lib/config';

	let { connector, class: className = '' }: { connector: Connector; class?: string } = $props();

	const icons = {
		server: ServerIcon,
		mail: MailIcon,
		inbox: InboxIcon,
		building: Building2Icon
	};

	let Icon = $derived(icons[connector.icon]);
</script>

<span
	class="grid size-10 shrink-0 place-items-center rounded-lg {connector.accent} {className}"
	aria-hidden="true"
>
	<Icon class="size-5" />
</span>
