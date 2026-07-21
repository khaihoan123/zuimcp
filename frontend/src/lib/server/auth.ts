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
		jwt(),
		// OAuth 2.1 authorization server (successor to the deprecated mcp
		// plugin): /oauth2/authorize, /oauth2/token, dynamic client
		// registration, PKCE. MCP clients (Claude, etc.) authorize against
		// this; FastAPI at /mcp/* is the protected resource.
		oauthProvider({
			loginPage: '/login',
			consentPage: '/consent',
			allowDynamicClientRegistration: true,
			// Audience the FastAPI MCP resource server expects in tokens.
			validAudiences: [process.env.MCP_RESOURCE_URL ?? 'http://localhost:8000'],
			scopes: ['openid', 'profile', 'email', 'offline_access']
		})
	]
});
