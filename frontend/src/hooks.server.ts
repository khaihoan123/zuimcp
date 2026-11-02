import { building } from '$app/environment';
import { auth } from '$lib/server/auth';
import { svelteKitHandler } from 'better-auth/svelte-kit';
import {
	oauthProviderAuthServerMetadata,
	oauthProviderOpenIdConfigMetadata
} from '@better-auth/oauth-provider';
import type { Handle } from '@sveltejs/kit';

// These are the OAuth-provider endpoints an MCP client calls cross-origin
// (e.g. Claude's own web app fetching them from https://claude.ai) BEFORE
// any user session exists — discovery, dynamic client registration, token
// exchange, JWKS. The `mcp` plugin this project migrated off (see auth.ts)
// set CORS headers for its equivalent routes; @better-auth/oauth-provider
// does not, so without this every cross-origin fetch to these paths is
// silently blocked by the browser, surfacing in Claude's UI as a generic
// "Couldn't register" with no server-side error to point at (curl doesn't
// enforce CORS, so testing with curl alone hides this entirely). Left off
// of session-cookie endpoints like /auth/sign-in — those should stay
// same-origin only.
const PUBLIC_OAUTH_PATHS = [
	'/.well-known/oauth-authorization-server',
	'/.well-known/openid-configuration',
	'/auth/oauth2/register',
	'/auth/oauth2/token',
	'/auth/oauth2/introspect',
	'/auth/oauth2/revoke',
	'/auth/jwks'
];

function isPublicOAuthPath(pathname: string): boolean {
	return PUBLIC_OAUTH_PATHS.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

function withCors(response: Response, request: Request): Response {
	const origin = request.headers.get('origin');
	if (origin) response.headers.set('Access-Control-Allow-Origin', origin);
	response.headers.set('Vary', 'Origin');
	return response;
}

export const handle: Handle = async ({ event, resolve }) => {
	const { pathname } = event.url;
	const isPublicOAuth = isPublicOAuthPath(pathname);

	if (isPublicOAuth && event.request.method === 'OPTIONS') {
		const requestHeaders = event.request.headers.get('access-control-request-headers');
		return withCors(
			new Response(null, {
				status: 204,
				headers: {
					'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
					'Access-Control-Allow-Headers': requestHeaders ?? 'Content-Type, Authorization',
					'Access-Control-Max-Age': '86400'
				}
			}),
			event.request
		);
	}

	// OAuth discovery must be served at the domain root, outside Better
	// Auth's /auth base path. MCP clients resolve these before authorizing.
	// Prefix match: RFC 8414 appends the issuer's path component, so
	// clients may request /.well-known/oauth-authorization-server/auth.
	if (pathname.startsWith('/.well-known/oauth-authorization-server')) {
		return withCors(await oauthProviderAuthServerMetadata(auth)(event.request), event.request);
	}
	if (pathname.startsWith('/.well-known/openid-configuration')) {
		return withCors(await oauthProviderOpenIdConfigMetadata(auth)(event.request), event.request);
	}

	const response = await svelteKitHandler({ event, resolve, auth, building });
	return isPublicOAuth ? withCors(response, event.request) : response;
};
