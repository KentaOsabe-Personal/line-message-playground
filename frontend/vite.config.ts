import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

const hostLabel = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/

export function validatePublicHost(value: string | undefined): string {
  if (
    value === undefined ||
    value.length === 0 ||
    value.length > 253 ||
    // HostのASCII範囲を検証するため、制御文字を含む範囲を明示する。
    // eslint-disable-next-line no-control-regex
    !/^[\x00-\x7F]+$/.test(value) ||
    value.trim() !== value ||
    /[:/?#*@[\]\\]/.test(value) ||
    value.split('.').some((label) => !hostLabel.test(label))
  ) {
    throw new Error('PUBLIC_HOST_INVALID')
  }

  return value
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'NGROK_DOMAIN')
  const allowedHosts = [validatePublicHost(env.NGROK_DOMAIN)]

  return {
    appType: 'spa',
    plugins: [react(), tailwindcss()],
    server: {
      allowedHosts,
      proxy: {
        '/api': {
          target: 'http://backend:8000',
          changeOrigin: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      include: ['test/**/*.test.{ts,tsx}'],
    },
  }
})
