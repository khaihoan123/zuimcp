/**
 * Single source of truth for platform naming, marketing navigation and the
 * connector catalogue. The brand name lives here so renaming the platform is a
 * one-line change.
 */
export const SITE_NAME = 'ZuiMCP';
export const SITE_TAGLINE = 'Controlled access to your tools, for AI agents.';

export type Availability = 'available' | 'soon';

/* ---------------------------------------------------------------------------
 * Marketing site
 * ------------------------------------------------------------------------- */

export type ProductLink = {
	label: string;
	description: string;
	availability: Availability;
	/** Omitted for categories that have no page yet — rendered as non-clickable. */
	href?: string;
};

export const products: ProductLink[] = [
	{
		href: '/products/email',
		label: 'Email',
		description: 'IMAP today; Gmail and Outlook via native APIs.',
		availability: 'available'
	},
	{
		label: 'ERPs',
		description: 'Orders, contacts and inventory from your business system.',
		availability: 'soon'
	}
];

export const marketingNav = [
	{ href: '/pricing', label: 'Pricing' },
	{ href: '/docs', label: 'Docs' },
	{ href: '/blog', label: 'Blog' }
];

/* ---------------------------------------------------------------------------
 * Connector catalogue (dashboard side)
 * ------------------------------------------------------------------------- */

/** Icon key, resolved to a component by the connector card. */
export type ConnectorIcon = 'server' | 'mail' | 'inbox' | 'building';

export type Connector = {
	/** Matches the `service_type` stored on a backend `connections` row. */
	id: string;
	label: string;
	description: string;
	availability: Availability;
	icon: ConnectorIcon;
	/** Tailwind classes for the logo tile. */
	accent: string;
};

export type ServiceCategory = {
	id: string;
	label: string;
	description: string;
	connectors: Connector[];
};

export const serviceCategories: ServiceCategory[] = [
	{
		id: 'email',
		label: 'Email',
		description: 'Mailboxes your agent can read, search and draft in.',
		connectors: [
			{
				id: 'imap',
				label: 'IMAP / SMTP',
				description: 'Any standards-compliant mailbox, using an app password.',
				availability: 'available',
				icon: 'server',
				accent: 'bg-indigo-500/10 text-indigo-500 dark:text-indigo-400'
			},
			{
				id: 'gmail',
				label: 'Gmail',
				description: 'Native Gmail API over OAuth — no app password needed.',
				availability: 'soon',
				icon: 'mail',
				accent: 'bg-red-500/10 text-red-500 dark:text-red-400'
			},
			{
				id: 'ms_graph',
				label: 'Outlook',
				description: 'Microsoft Graph, including Microsoft 365 accounts.',
				availability: 'soon',
				icon: 'inbox',
				accent: 'bg-sky-500/10 text-sky-500 dark:text-sky-400'
			}
		]
	},
	{
		id: 'erp',
		label: 'ERPs',
		description: 'Orders, contacts and inventory from your business system.',
		connectors: [
			{
				id: 'odoo',
				label: 'Odoo',
				description: 'Odoo 8 to 19 — Online, Odoo.sh or self-hosted, Community or Enterprise.',
				availability: 'available',
				icon: 'building',
				accent: 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
			}
		]
	}
];

/** Flat list of every connector, for lookups by `service_type`. */
export const allConnectors: Connector[] = serviceCategories.flatMap((c) => c.connectors);

export function connectorFor(serviceType: string): Connector | undefined {
	return allConnectors.find((c) => c.id === serviceType);
}

/** Display label for a stored `service_type`, falling back to the raw value. */
export function serviceLabel(serviceType: string): string {
	return connectorFor(serviceType)?.label ?? serviceType;
}

/** Human labels for the namespaced capability strings stored per connection. */
export const capabilityLabels: Record<string, string> = {
	'email.read': 'Read emails',
	'email.search': 'Search emails',
	'email.mark_seen': 'Mark read / unread',
	'email.draft': 'Create drafts',
	'email.send': 'Send emails',
	'odoo.read': 'Read records',
	'odoo.create': 'Create records',
	'odoo.write': 'Update records',
	'odoo.delete': 'Delete records'
};

export function capabilityLabel(capability: string): string {
	return capabilityLabels[capability] ?? capability;
}

/** Human labels for the `status` field on an /api/audit-log entry. */
export const auditStatusLabels: Record<string, string> = {
	success: 'Success',
	rate_limited: 'Rate limited',
	error: 'Error'
};

export function auditStatusLabel(status: string): string {
	return auditStatusLabels[status] ?? status;
}

const capabilityOrder = Object.keys(capabilityLabels);

/**
 * Stable display order for capability toggles. The API returns permissions in
 * whatever order the database yields — which changes after an update — so sort
 * before rendering or the checkboxes visibly reshuffle under the user.
 */
export function sortPermissions<T extends { capability: string }>(permissions: T[]): T[] {
	const rank = (capability: string) => {
		const i = capabilityOrder.indexOf(capability);
		return i === -1 ? capabilityOrder.length : i;
	};
	return [...permissions].sort(
		(a, b) => rank(a.capability) - rank(b.capability) || a.capability.localeCompare(b.capability)
	);
}
