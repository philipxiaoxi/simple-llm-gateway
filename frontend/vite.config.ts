import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      injectRegister: 'inline',
      includeAssets: ['favicon.svg', 'apple-touch-icon.png'],
      manifest: {
        name: '中转台',
        short_name: '中转台',
        description: 'LLM Gateway 管理后台',
        theme_color: '#0b0d11',
        background_color: '#0b0d11',
        display: 'standalone',
        start_url: '/',
        scope: '/',
        icons: [
          {
            src: 'pwa-192x192.png',
            sizes: '192x192',
            type: 'image/png',
          },
          {
            src: 'pwa-512x512.png',
            sizes: '512x512',
            type: 'image/png',
            purpose: 'any maskable',
          },
        ],
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        navigateFallbackDenylist: [/^\/api\//, /^\/v1\//, /^\/anthropic\//, /^\/chat\//, /^\/responses\//, /^\/models\//, /^\/health$/],
      },
    }),
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/v1': 'http://127.0.0.1:8000',
      '/anthropic': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/chat': 'http://127.0.0.1:8000',
      '/responses': 'http://127.0.0.1:8000',
      '/models': 'http://127.0.0.1:8000',
    },
  },
})
