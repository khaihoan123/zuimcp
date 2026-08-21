import adapter from '@sveltejs/adapter-node';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig, loadEnv } from 'vite';
import basicSsl from '@vitejs/plugin-basic-ssl';

export default defineConfig(({ mode }) => {
	// Loaded here (not just inside the plugin below) because server.allowedHosts
	// needs it immediately — the load-server-env plugin's config() hook only
	// runs later, during Vite's own config resolution, too late for this
	// factory function's return value to see it.
	const env = loadEnv(mode, process.cwd(), '');
	// Terminates HTTPS locally with a self-signed cert whenever a tunnel
	// hostname is configured (DEV_ALLOWED_HOSTS). Better Auth's svelteKitHandler
	// (isAuthPath) compares the request's actual origin against BETTER_AUTH_URL
	// — if that's https but Vite's dev server only ever speaks plain http, the
	// scheme mismatch makes every /auth/* route silently 404 (falls through to
	// SvelteKit's router instead of Better Auth). Off for normal local dev.
	const tunnelTesting = Boolean(env.DEV_ALLOWED_HOSTS);

	return {
		plugins: [
			...(tunnelTesting ? [basicSsl()] : []),
			// Expose non-VITE_ vars from .env (DATABASE_URL, BETTER_AUTH_*) to
			// server-side code via process.env; production reads the real environment.
			{
				name: 'load-server-env',
				config() {
					Object.assign(process.env, env);
				}
			},
			tailwindcss(),
			sveltekit({
				compilerOptions: {
					// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
					runes: ({ filename }) =>
						filename.split(/[/\\]/).includes('node_modules') ? undefined : true
				},

				adapter: adapter()
			})
		],
		server: {
			// Lets a local Tailscale Funnel/serve hostname through — Vite rejects
			// any Host header it doesn't recognize by default. Comma-separated,
			// only set while testing over a tunnel.
			allowedHosts: (env.DEV_ALLOWED_HOSTS ?? '')
				.split(',')
				.map((h) => h.trim())
				.filter(Boolean),
			proxy: {
				'/api': {
					target: 'http://127.0.0.1:8000',
					changeOrigin: true
				}
			}
		}
	};
});
