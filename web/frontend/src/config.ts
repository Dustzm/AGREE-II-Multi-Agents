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
    return `${protocol}//${window.location.host}${APP_BASE_PATH}/api`
})()
