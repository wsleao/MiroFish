import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

// Configuração oficial de produção para compilar o Frontend Vue 3 na Vercel
export default defineConfig({
  plugins: [vue()], // IMPORTANTE: Ativa o interpretador de arquivos .vue
  server: {
    allowedHosts: true,
    host: '0.0.0.0',
    port: process.env.PORT || 3000
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'), // Mapeia a pasta raiz do código
    }
  }
})
