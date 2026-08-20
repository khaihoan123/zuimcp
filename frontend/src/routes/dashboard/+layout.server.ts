import { redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import type { LayoutServerLoad } from './$types';

// Guards the whole dashboard section — every page under /dashboard requires a session.
export const load: LayoutServerLoad = async ({ parent }) => {
	const { user } = await parent();
	if (!user) {
		redirect(302, '/login');
	}

	return {
		user,
		// One endpoint PER service, keyed by service category id — each service runs its
		// own MCP server (/mcp/email, /mcp/odoo …). The email value is the same string the
		// backend computes as RESOURCE_INDICATOR (MCP_RESOURCE_URL + MCP_PATH) and that
		// Better Auth lists in validAudiences, so read the env var rather than rebuild it.
		mcpEndpoints: {
			email: env.MCP_RESOURCE_INDICATOR || 'http://localhost:8000/mcp/email'
		} as Record<string, string | undefined>
	};
};
