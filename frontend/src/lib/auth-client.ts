import { browser } from '$app/environment';
import { createAuthClient } from 'better-auth/svelte';
import { oauthProviderClient } from '@better-auth/oauth-provider/client';

// baseURL must be absolute. The SSR-constructed instance never issues
// requests (all sign-in/out calls run in the browser), so the server
// fallback just has to be a well-formed URL.
export const authClient = createAuthClient({
	baseURL: browser ? `${window.location.origin}/auth` : 'http://localhost:5173/auth',
	plugins: [oauthProviderClient()]
});
