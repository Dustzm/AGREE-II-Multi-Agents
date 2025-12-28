/**
 * 提示词管理 API 服务
 */
import { API_URL } from '@/config'

const API_BASE = API_URL

export interface PromptData {
    agent_name: string
    lang: string
    content: string
    variables: string[]
}

export const promptsService = {
    /**
     * 获取所有智能体列表
     */
    async list(): Promise<string[]> {
        const response = await fetch(`${API_BASE}/prompts`)
        if (!response.ok) {
            throw new Error('获取智能体列表失败')
        }
        return response.json()
    },

    /**
     * 获取指定智能体的提示词
     */
    async get(agentName: string, lang: string): Promise<PromptData> {
        const response = await fetch(`${API_BASE}/prompts/${agentName}?lang=${lang}`)
        if (!response.ok) {
            throw new Error('获取提示词失败')
        }
        return response.json()
    },

    /**
     * 更新指定智能体的提示词
     */
    async update(agentName: string, lang: string, content: string): Promise<PromptData> {
        const response = await fetch(`${API_BASE}/prompts/${agentName}?lang=${lang}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        })
        if (!response.ok) {
            throw new Error('更新提示词失败')
        }
        return response.json()
    }
}
