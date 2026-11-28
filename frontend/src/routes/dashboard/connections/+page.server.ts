import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// The page was renamed to Connectors; keep old links and bookmarks working.
export const load: PageServerLoad = ({ url }) => {
	redirect(308, `/dashboard/connectors${url.search}`);
};
