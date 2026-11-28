import { auth } from '$lib/server/auth';
import type { PageServerLoad } from './$types';

export type ConsentClient = {
	name: string | null;
	logo: string | null;
	website: string | null;
};

export const load: PageServerLoad = async ({ url, request }) => {
	const clientId = url.searchParams.get('client_id') ?? '';

	// The name and logo are whatever the app sent when it registered itself, so
	// anyone can claim to be "Claude". The redirect host is different: the token
	// can only be delivered there, so the page shows it as the trustworthy part.
	let redirectHost: string | null = null;
	try {
		const redirect = url.searchParams.get('redirect_uri');
		if (redirect) redirectHost = new URL(redirect).host;
	} catch {
		redirectHost = null;
	}

	let client: ConsentClient = { name: null, logo: null, website: null };
	if (clientId) {
		try {
			const info = await auth.api.getOAuthClientPublic({
				query: { client_id: clientId },
				headers: request.headers
			});
			client = {
				name: info.client_name ?? null,
				logo: httpsOnly(info.logo_uri),
				website: httpsOnly(info.client_uri)
			};
		} catch {
			// Unknown or disabled client: the page falls back to a generic name,
			// and the consent call itself will fail with Better Auth's error.
		}
	}

	// Apps like Claude register without a logo_uri. Fall back to the favicon of
	// the site the user is sent back to, which is the verified part anyway.
	if (!client.logo && redirectHost && url.searchParams.get('redirect_uri')?.startsWith('https://')) {
		client.logo = `https://${redirectHost}/favicon.ico`;
	}

	return { client, redirectHost };
};

// Only https URLs from the client's self-reported metadata get rendered.
function httpsOnly(value: string | undefined | null): string | null {
	if (!value) return null;
	try {
		return new URL(value).protocol === 'https:' ? value : null;
	} catch {
		return null;
	}
}
