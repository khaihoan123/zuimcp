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

// Each provider is only registered if both its env vars are set, so a dev
// box without Google/GitHub OAuth apps configured still gets working
// email/password auth instead of Better Auth throwing on a blank secret.
// Redirect URI to register with each provider's console:
// {BETTER_AUTH_URL}/auth/callback/google (or /github).
const socialProviders: NonNullable<Parameters<typeof betterAuth>[0]['socialProviders']> = {};
if (process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET) {
	socialProviders.google = {
		clientId: process.env.GOOGLE_CLIENT_ID,
		clientSecret: process.env.GOOGLE_CLIENT_SECRET
	};
}
if (process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET) {
	socialProviders.github = {
		clientId: process.env.GITHUB_CLIENT_ID,
		clientSecret: process.env.GITHUB_CLIENT_SECRET
	};
}

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
	account: {
		accountLinking: {
			enabled: true,
			// Google/GitHub verify the email themselves before returning it, so
			// if it matches an existing user we link this login method onto
			// that account instead of erroring with account_not_linked. Without
			// this, anyone who originally signed up with email/password can
			// never also sign in via a social provider using that same address.
			trustedProviders: ['google', 'github']
		}
	},
	// Sign-in via Google/GitHub (Better Auth acting as an OAuth *client*) —
	// distinct from the oauthProvider plugin below, where Better Auth is the
	// OAuth *server* that MCP clients authorize against. Unrelated flows,
	// same library.
	socialProviders,
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
		// this; FastAPI hosts ONE protected resource, /mcp, which serves
		// every service's tools (email_*, odoo_* ...).
		oauthProvider({
			loginPage: '/login',
			consentPage: '/consent',
			allowDynamicClientRegistration: true,
			// Without this, /oauth2/register 401s with "Authentication
			// required for client registration" — it defaults to requiring a
			// session, but an MCP client (Claude, etc.) registers itself
			// BEFORE any user is authenticated; that's the entire point of
			// dynamic client registration. Public clients only (no client
			// secret issued), which is correct for this flow.
			allowUnauthenticatedClientRegistration: true,
			// The protected resources tokens may be issued for. Must exactly
			// equal the "resource" value an MCP client requests (RFC 8707):
			// FastAPI's base URL + its /mcp path, NOT the bare origin. Any
			// other value is refused with "requested resource ... is not
			// configured". Seeded into the oauthResource table at startup
			// (insert-only, so a changed URL is added, never overwritten).
			// Replaces `validAudiences`, which newer versions ignore.
			resources: [process.env.MCP_RESOURCE_INDICATOR ?? 'http://localhost:8000/mcp'],
			// Dynamically registered clients (Claude, etc.) can't be linked to
			// a resource by hand, so any registered client may request /mcp.
			// What the agent can actually do is still limited per account by
			// the user's permissions in the backend.
			enforcePerClientResources: false,
			scopes: ['openid', 'profile', 'email', 'offline_access']
		})
	]
});
