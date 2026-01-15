import { API_URL } from '@/config'

const API_BASE = API_URL

export interface UploadResponse {
    task_id: string
    filename: string
    file_path: string
}

export interface PdfFile {
    name: string
    path: string
    size: number
    modified: string
}

export interface FolderInfo {
    name: string
    path: string
    files: PdfFile[]
}

export interface PdfListResponse {
    folders: FolderInfo[]
    root_files: PdfFile[]
    input_dir: string
}

export interface StartTaskResponse {
    task_id: string
    pdf_name: string
    output_dir: string
}

export interface UploadToInputResponse {
    filename: string
    path: string
    size: number
}

export interface UploadedFile {
    filename: string
    relative_path: string
    absolute_path: string
    size: number
}

export interface UploadFolderResponse {
    total_count: number
    success_count: number
    failed_count: number
    uploaded_files: UploadedFile[]
    failed_files: Array<{ filename: string; error: string }>
}

export interface ReportSummary {
    task_id: string
    title: string
    date: string
    quality_score: number | null  // 总体质量评分 (1-7)
    recommendation: string | null
}

export interface BatchFolderInfo {
    name: string
    report_count: number
}

export interface ReportListResponse {
    folders: BatchFolderInfo[]
    reports: ReportSummary[]
    current_folder: string | null
}

export interface DomainScore {
    name: string
    score: number | null
    item_count: number
    evaluated_count: number
}

export interface ItemDetail {
    item_id: string
    domain: number
    title: string
    score: number | null
    reasoning_summary: string | null
}

export interface ItemEvaluation {
    item_id: string
    domain: number
    title: string
    score: number | null
    step1_locate: string | null
    step2_evidence: string[] | null
    step3_reasoning: string | null
    evaluated_at: string | null
}

export interface ReportDetail {
    task_id: string
    guideline_title: string
    evaluation_date: string
    evaluator: string
    domain_scores: Record<string, DomainScore>
    overall_assessment: {
        strengths: string[]
        weaknesses: string[]
        recommendation: string
    }
    item_evaluations: ItemEvaluation[]
    total_raw_score?: number  // 总分（所有条目原始分数总和）
    overall_quality_score?: number  // 总体质量评分 (1-7)
}

export const api = {
    // 列出 input 目录下的 PDF 文件
    async listPdfFiles(): Promise<PdfListResponse> {
        const response = await fetch(`${API_BASE}/files/list`)
        if (!response.ok) {
            throw new Error('获取文件列表失败')
        }
        return response.json()
    },

    // 启动评价任务
    async startTask(pdfPath: string): Promise<StartTaskResponse> {
        const response = await fetch(`${API_BASE}/files/start`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pdf_path: pdfPath }),
        })
        if (!response.ok) {
            throw new Error('启动任务失败')
        }
        return response.json()
    },

    // 上传 PDF 文件（保留兼容）
    async uploadPdf(file: File): Promise<UploadResponse> {
        const formData = new FormData()
        formData.append('file', file)

        const response = await fetch(`${API_BASE}/upload`, {
            method: 'POST',
            body: formData,
        })

        if (!response.ok) {
            throw new Error('上传失败')
        }

        return response.json()
    },

    // 上传 PDF 文件到 input 目录
    async uploadPdfToInput(file: File): Promise<UploadToInputResponse> {
        const formData = new FormData()
        formData.append('file', file)

        const response = await fetch(`${API_BASE}/files/upload`, {
            method: 'POST',
            body: formData,
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '上传失败' }))
            throw new Error(error.detail || '上传失败')
        }

        return response.json()
    },

    // 上传文件夹中的 PDF 文件
    async uploadFolder(files: File[]): Promise<UploadFolderResponse> {
        const formData = new FormData()

        // 过滤并添加 PDF 文件
        const pdfFiles = files.filter(f => f.name.toLowerCase().endsWith('.pdf'))

        pdfFiles.forEach(file => {
            // 使用 webkitRelativePath 作为 filename，保持目录结构
            const relativePath = (file as any).webkitRelativePath || file.name
            formData.append('files', file, relativePath)
        })

        const response = await fetch(`${API_BASE}/files/upload-folder`, {
            method: 'POST',
            body: formData,
        })

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '上传失败' }))
            throw new Error(error.detail || '上传失败')
        }

        return response.json()
    },

    // 获取报告列表
    async listReports(folder?: string): Promise<ReportListResponse> {
        const params = folder ? `?folder=${encodeURIComponent(folder)}` : ''
        const response = await fetch(`${API_BASE}/reports${params}`)
        if (!response.ok) {
            throw new Error('获取报告列表失败')
        }
        return response.json()
    },

    // 获取报告详情
    async getReport(taskId: string, folder?: string): Promise<ReportDetail> {
        const params = folder ? `?folder=${encodeURIComponent(folder)}` : ''
        const response = await fetch(`${API_BASE}/reports/${taskId}${params}`)
        if (!response.ok) {
            throw new Error('获取报告详情失败')
        }
        return response.json()
    },

    // 获取系统健康状态（包含启动时间）
    async getHealth(): Promise<{ status: string, startup_time?: number }> {
        // health 路由挂载在 /ama/health，不是 /ama/api/health
        // API_BASE 是 /ama/api，所以需要替换
        const url = API_BASE.replace('/api', '/health')
        const response = await fetch(url)
        if (!response.ok) {
            throw new Error('健康检查失败')
        }
        return response.json()
    }
}
