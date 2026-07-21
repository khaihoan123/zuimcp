import { building } from '$app/environment';
import { auth } from '$lib/server/auth';
import { svelteKitHandler } from 'better-auth/svelte-kit';
import {
	oauthProviderAuthServerMetadata,
	oauthProviderOpenIdConfigMetadata
} from '@better-auth/oauth-provider';
import type { Handle } from '@sveltejs/kit';

export const handle: Handle = async ({ event, resolve }) => {
	// OAuth discovery must be served at the domain root, outside Better
	// Auth's /auth base path. MCP clients resolve these before authorizing.
	// Prefix match: RFC 8414 appends the issuer's path component, so
	// clients may request /.well-known/oauth-authorization-server/auth.
	if (event.url.pathname.startsWith('/.well-known/oauth-authorization-server')) {
		return oauthProviderAuthServerMetadata(auth)(event.request);
	}
	if (event.url.pathname.startsWith('/.well-known/openid-configuration')) {
		return oauthProviderOpenIdConfigMetadata(auth)(event.request);
	}
	return svelteKitHandler({ event, resolve, auth, building });
};
