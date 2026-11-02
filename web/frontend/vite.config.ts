import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// ============================================
// 【统一配置】
// ============================================
const APP_BASE_PATH = '/ama'              // 应用基础路径

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
    const env = loadEnv(mode, process.cwd(), '')
    const backendUrl = env.VITE_BACKEND_URL || process.env.VITE_BACKEND_URL || 'http://localhost:8011'
    const frontendPort = Number(env.VITE_FRONTEND_PORT || process.env.VITE_FRONTEND_PORT || 8012)

    return {
        base: `${APP_BASE_PATH}/`,
        plugins: [
            react(),
            tailwindcss(),
            // 添加自定义插件处理 /ama 到 /ama/ 的重定向
            {
                name: 'redirect-base-path',
                configureServer(server) {
                    server.middlewares.use((req, res, next) => {
                        // 如果访问 /ama (不带尾部斜杠)，重定向到 /ama/
                        if (req.url === APP_BASE_PATH) {
                            res.writeHead(302, { Location: `${APP_BASE_PATH}/` })
                            res.end()
                            return
                        }
                        next()
                    })
                },
            },
        ],
        resolve: {
            alias: {
                '@': path.resolve(__dirname, './src'),
            },
        },
        server: {
            host: '0.0.0.0',
            port: frontendPort,
            allowedHosts: true,  // 允许所有域名访问（生产环境建议使用构建后的静态文件）
            proxy: {
                [`${APP_BASE_PATH}/api`]: {
                    target: backendUrl,
                    changeOrigin: true,
                    ws: true,  // 启用 WebSocket 代理
                },
                [`${APP_BASE_PATH}/health`]: {
                    target: backendUrl,
                    changeOrigin: true,
                },
                [`${APP_BASE_PATH}/ready`]: {
                    target: backendUrl,
                    changeOrigin: true,
                },
            },
        },
    }
})
