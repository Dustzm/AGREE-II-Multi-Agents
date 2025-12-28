/**
 * 前端全局配置
 * 
 * 修改 APP_BASE_PATH 即可统一更改所有路由前缀
 */

// ============================================
// 【唯一配置项】应用基础路径 - 用于 K8s nginx 转发
// ============================================
export const APP_BASE_PATH = '/ama'

// ============================================
// 派生配置（无需修改）
// ============================================

// API 完整路径
export const API_URL = `${APP_BASE_PATH}/api`

// WebSocket URL
export const WS_URL = (() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const hostname = window.location.hostname
    const port = window.location.port

    // 生产环境：通过 nginx 代理，使用相同 host（无需指定端口）
    // 开发环境：前端 8012，后端 8011
    if (!port || port === '80' || port === '443') {
        // 生产环境，WebSocket 通过 nginx 代理
        return `${protocol}//${hostname}${APP_BASE_PATH}/api`
    } else if (port === '8012') {
        // 开发环境
        return `${protocol}//${hostname}:8011${APP_BASE_PATH}/api`
    } else {
        // 其他情况使用相同端口
        return `${protocol}//${hostname}:${port}${APP_BASE_PATH}/api`
    }
})()
