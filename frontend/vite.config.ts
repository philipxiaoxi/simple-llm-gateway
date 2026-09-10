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
      injectRegister: false,
      // 图标由浏览器按需请求：includeAssets / includeManifestIcons 会以
      // additionalManifestEntries 的形式加入预缓存，globIgnores 拦不住，故一并关闭。
      includeAssets: [],
      includeManifestIcons: false,
      manifest: {
        name: 'AI一体化服务平台',
        short_name: 'AI一体化服务平台',
        description: 'AI一体化服务平台',
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
        // index.html 也进预缓存，作为离线导航的兜底外壳
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // 图标体积大且浏览器会自行请求，不进预缓存
        globIgnores: [
          '**/pwa-*.png',
          '**/apple-touch-icon.png',
          '**/favicon.png',
          '**/favicon.ico',
          '**/favicon.svg',
        ],
        navigateFallback: '/index.html',
        cleanupOutdatedCaches: true,
        skipWaiting: true,
        clientsClaim: true,
        // 导航由预缓存的 index.html 外壳接管（NavigationRoute 先注册且先匹配），
        // 这里不再配置 NetworkFirst，避免留下一段永远不会执行的规则。
      },
    }),
  ],
  build: {
    rolldownOptions: {
      output: {
        // 业务代码改版不失效的公共依赖 chunk
        codeSplitting: {
          groups: [{ name: 'vendor', test: /[\\/]node_modules[\\/]/ }],
        },
      },
    },
  },
  server: {
    port: 5173,
    host: '0.0.0.0',
    allowedHosts: ['.monkeycode-ai.online'],
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
