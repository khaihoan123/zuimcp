import { betterAuth } from 'better-auth';
import { jwt } from 'better-auth/plugins';
import { oauthProvider } from '@better-auth/oauth-provider';
import pg from 'pg';

// process.env is populated from .env by vite.config.ts in dev,
// and by the real environment in production. The Better Auth CLI
// (npx @better-auth/cli migrate) reads .env itself.
const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
	throw new Error('DATABASE_URL is not set');
}

const baseURL = process.env.BETTER_AUTH_URL ?? 'http://localhost:5173';

export const auth = betterAuth({
	baseURL,
	// Default is /api/auth, but the Vite proxy sends /api/* to FastAPI,
	// so auth endpoints live under /auth instead.
	basePath: '/auth',
	secret: process.env.BETTER_AUTH_SECRET,
	database: new pg.Pool({ connectionString }),
	emailAndPassword: {
		enabled: true
	},
	// The jwt plugin's own /token path is superseded by the OAuth 2.1
	// token endpoint at /oauth2/token.
	disabledPaths: ['/token'],
	plugins: [
		// Signs OAuth access tokens and publishes real keys at /auth/jwks —
		// this is what lets the FastAPI resource server verify tokens.
		// RS256 instead of the EdDSA default: FastMCP's JWTVerifier (used on
		// the FastAPI side) only supports RS/ES/PS/HS families, not EdDSA.
		jwt({
			jwks: {
				keyPairConfig: { alg: 'RS256', modulusLength: 2048 }
			}
		}),
		// OAuth 2.1 authorization server (successor to the deprecated mcp
		// plugin): /oauth2/authorize, /oauth2/token, dynamic client
		// registration, PKCE. MCP clients (Claude, etc.) authorize against
		// this; FastAPI hosts one protected resource per service, each
		// under /mcp/<service> (e.g. /mcp/email, /mcp/odoo later).
		oauthProvider({
			loginPage: '/login',
			consentPage: '/consent',
			allowDynamicClientRegistration: true,
			// Must exactly equal the "resource" value an MCP client requests
			// (RFC 8707), which is the full protected-resource identifier —
			// i.e. FastAPI's base URL + its /mcp/<service> path, NOT the bare
			// origin. Mismatch here fails token exchange with "requested
			// resource invalid" and silently falls back to an opaque
			// (non-JWT) access_token instead of erroring, which is easy to
			// miss. Add one entry per service as they're built (Odoo, etc.).
			validAudiences: [process.env.MCP_RESOURCE_INDICATOR ?? 'http://localhost:8000/mcp/email'],
			scopes: ['openid', 'profile', 'email', 'offline_access']
		})
	]
});
