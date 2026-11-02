/**
 * 提示词管理 API 服务
 */
import { API_URL } from '@/config'
import { apiFetch, readJson } from '@/services/http'

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
        const response = await apiFetch(`${API_BASE}/prompts`)

        return readJson(response)
    },

    /**
     * 获取指定智能体的提示词
     */
    async get(agentName: string, lang: string): Promise<PromptData> {
        const response = await apiFetch(`${API_BASE}/prompts/${agentName}?lang=${lang}`)

        return readJson(response)
    },

    /**
     * 更新指定智能体的提示词
     */
    async update(agentName: string, lang: string, content: string): Promise<PromptData> {
        const response = await apiFetch(`${API_BASE}/prompts/${agentName}?lang=${lang}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content })
        })

        return readJson(response)
    }
}
