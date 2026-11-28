<script lang="ts">
	// The ZuiMCP mark: a "Z" whose two ends are connection points, on an indigo
	// tile. Keep in sync with $lib/assets/favicon.svg.
	let { class: className = 'size-7' }: { class?: string } = $props();
	// Unique per instance: the header and footer both render the mark.
	const uid = $props.id();
	const gradientId = `zui-mark-${uid}`;
</script>

<svg viewBox="0 0 32 32" class={className} aria-hidden="true">
	<defs>
		<linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
			<stop offset="0" stop-color="#818cf8" />
			<stop offset="1" stop-color="#4f46e5" />
		</linearGradient>
	</defs>
	<rect width="32" height="32" rx="8" fill="url(#{gradientId})" />
	<path
		d="M10.5 10h11L10.5 22h11"
		fill="none"
		stroke="#fff"
		stroke-width="3.2"
		stroke-linecap="round"
		stroke-linejoin="round"
	/>
	<circle cx="10.5" cy="10" r="2.6" fill="#fff" />
	<circle cx="21.5" cy="22" r="2.6" fill="#fff" />
</svg>
