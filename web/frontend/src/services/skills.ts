import { API_URL } from '@/config'
import { apiFetch, readJson } from '@/services/http'

const API_BASE_URL = API_URL

export interface SkillScoringPrinciple {
    range: string | number
    condition?: string
    condition_zh?: string
    [key: string]: unknown
}

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
    scoring_principle: SkillScoringPrinciple[]
}

export interface SkillUpdateDetails {
    title_zh: string
    description_zh: string
    locating_info_zh: string
    title: string
    description: string
    locating_info: string
    criteria: string[]
    scoring_principle: SkillScoringPrinciple[]
}

export const skillsService = {
    async getAll(): Promise<Skill[]> {
        const response = await apiFetch(`${API_BASE_URL}/skills`)

        return readJson(response)
    },

    async updateStatus(id: string, enabled: boolean): Promise<Skill> {
        const response = await apiFetch(`${API_BASE_URL}/skills/${id}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ enabled }),
        })

        return readJson(response)
    },

    async updateDetails(id: string, details: SkillUpdateDetails): Promise<Skill> {
        const response = await apiFetch(`${API_BASE_URL}/skills/${id}/details`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(details),
        })

        return readJson(response)
    },

    /**
     * 批量更新所有 skills 的启用状态
     */
    async batchUpdateStatus(enabled: boolean): Promise<Skill[]> {
        const response = await apiFetch(`${API_BASE_URL}/skills/batch/status`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ enabled }),
        })

        return readJson(response)
    }
}
