import { API_URL } from '@/config'

const API_BASE_URL = API_URL

export interface Skill {
    item_id: string
    enabled: boolean
    domain: number
    domain_name: string
    title: string
    description: string
    locating_info: string
    // Full fields for editing
    title_zh?: string
    description_zh?: string
    locating_info_zh?: string
    title_en?: string
    description_en?: string
    locating_info_en?: string
    criteria: string[]
    scoring_principle: Record<string, any>[]
}

export interface SkillUpdateDetails {
    title_zh: string
    description_zh: string
    locating_info_zh: string
    title: string
    description: string
    locating_info: string
    criteria: string[]
    scoring_principle: Record<string, any>[]
}

export const skillsService = {
    async getAll(): Promise<Skill[]> {
        // 获取当前语言并转换为后端格式 (zh-CN -> zh, en-US -> en)
        const currentLang = document.documentElement.lang || 'en'
        const lang = currentLang.split('-')[0]

        const response = await fetch(`${API_BASE_URL}/skills`, {
            headers: {
                'Accept-Language': lang
            }
        })
        if (!response.ok) {
            throw new Error('Failed to fetch skills')
        }
        return response.json()
    },

    async updateStatus(id: string, enabled: boolean): Promise<Skill> {
        const response = await fetch(`${API_BASE_URL}/skills/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ enabled }),
        })
        if (!response.ok) {
            throw new Error('Failed to update skill status')
        }
        return response.json()
    },

    async updateDetails(id: string, details: SkillUpdateDetails): Promise<Skill> {
        const response = await fetch(`${API_BASE_URL}/skills/${id}/details`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(details),
        })
        if (!response.ok) {
            throw new Error('Failed to update skill details')
        }
        return response.json()
    },

    /**
     * 批量更新所有 skills 的启用状态
     */
    async batchUpdateStatus(enabled: boolean): Promise<Skill[]> {
        const response = await fetch(`${API_BASE_URL}/skills/batch/status`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ enabled }),
        })
        if (!response.ok) {
            throw new Error('Failed to batch update skills')
        }
        return response.json()
    }
}

