import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': 'http://localhost:11451',
      '/auth': 'http://localhost:11451',
      '/stats': 'http://localhost:11451',
      '/accounts': 'http://localhost:11451',
      '/rotation': 'http://localhost:11451',
      '/config': 'http://localhost:11451',
      '/api-keys': 'http://localhost:11451',
      '/health': 'http://localhost:11451',
    },
  },
  build: {
    outDir: path.resolve(__dirname, '../src/aistudio_api/static'),
    emptyOutDir: true,
    rollupOptions: {
      output: {
        entryFileNames: 'assets/[name].js',
        chunkFileNames: 'assets/[name].js',
        assetFileNames: 'assets/[name].[ext]',
        manualChunks: {
          'vendor-vue': ['vue', 'vue-router', 'pinia'],
          'vendor-icons': ['lucide-vue-next'],
          'vendor-yaml': ['yaml'],
        },
      },
    },
  },
})
