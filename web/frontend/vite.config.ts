import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// ============================================
// 【统一配置】
// ============================================
const APP_BASE_PATH = '/ama'              // 应用基础路径
const BACKEND_URL = 'http://localhost:8011' // 后端服务地址

// https://vite.dev/config/
export default defineConfig({
    base: `${APP_BASE_PATH}/`,
    plugins: [react(), tailwindcss()],
    resolve: {
        alias: {
            '@': path.resolve(__dirname, './src'),
        },
    },
    server: {
        host: '0.0.0.0',
        port: 8012,
        allowedHosts: true,  // 允许所有域名访问（生产环境建议使用构建后的静态文件）
        proxy: {
            [`${APP_BASE_PATH}/api`]: {
                target: BACKEND_URL,
                changeOrigin: true,
            },
            [`${APP_BASE_PATH}/health`]: {
                target: BACKEND_URL,
                changeOrigin: true,
            },
            [`${APP_BASE_PATH}/ready`]: {
                target: BACKEND_URL,
                changeOrigin: true,
            },
        },
    },
})
