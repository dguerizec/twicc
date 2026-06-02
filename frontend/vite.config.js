import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Backend port for proxy (passed by devctl.py via environment)
const backendPort = process.env.BACKEND_PORT || '3500'

// Optional: allow a custom host for dev tunnels (e.g. ngrok, Cloudflare Tunnel)
const devAllowedHost = process.env.DEV_HOSTNAME

export default defineConfig(({ command }) => ({
    plugins: [
        vue({
            template: {
                compilerOptions: {
                    isCustomElement: (tag) => tag.startsWith('wa-')
                }
            }
        })
    ],
    // Use /static/ base only for production build (Django serves static files)
    // In dev mode, use root path
    base: command === 'build' ? '/static/' : '/',
    build: {
        outDir: '../src/twicc/static/frontend',
        emptyOutDir: true
    },
    // Ensure all CodeMirror packages and their shared dependency (style-mod)
    // are pre-bundled together. Without this, Vite's dep optimizer may fail
    // to resolve style-mod, preventing CM6 from injecting its CSS.
    optimizeDeps: {
        include: [
            'codemirror',
            '@codemirror/state',
            '@codemirror/view',
            '@codemirror/language',
            '@codemirror/merge',
            '@codemirror/autocomplete',
            'style-mod',
        ],
    },
    server: {
        allowedHosts: devAllowedHost ? [devAllowedHost] : [],
        proxy: {
            '/api': `http://localhost:${backendPort}`,
            '/artifacts': `http://localhost:${backendPort}`,
            '/ws': { target: `ws://localhost:${backendPort}`, ws: true }
        }
    }
}))
