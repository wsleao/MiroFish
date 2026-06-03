import { defineConfig } from 'vite'

export default defineConfig({
  server: {
    allowedHosts: true, // Libera todos os hosts, incluindo o do Render
    host: '0.0.0.0',    // Permite que o container Docker escute a rede externa
    port: process.env.PORT || 5173
  }
})
