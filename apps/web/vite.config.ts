import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiHost = process.env.STOCKTRADE_API_HOST ?? '127.0.0.1'
const apiPort = process.env.STOCKTRADE_API_PORT ?? '8000'
const apiTarget = `http://${apiHost}:${apiPort}`

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
