const API_BASE = ''

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

export interface PdfListResponse {
    files: PdfFile[]
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

export interface ReportSummary {
    task_id: string
    title: string
    date: string
    score: number | null
    recommendation: string | null
}

export interface ReportListResponse {
    reports: ReportSummary[]
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
}

export const api = {
    // 列出 input 目录下的 PDF 文件
    async listPdfFiles(): Promise<PdfListResponse> {
        const response = await fetch(`${API_BASE}/api/files/list`)
        if (!response.ok) {
            throw new Error('获取文件列表失败')
        }
        return response.json()
    },

    // 启动评价任务
    async startTask(pdfPath: string): Promise<StartTaskResponse> {
        const response = await fetch(`${API_BASE}/api/files/start`, {
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

        const response = await fetch(`${API_BASE}/api/upload`, {
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

        const response = await fetch(`${API_BASE}/api/files/upload`, {
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
    async listReports(): Promise<ReportListResponse> {
        const response = await fetch(`${API_BASE}/api/reports`)
        if (!response.ok) {
            throw new Error('获取报告列表失败')
        }
        return response.json()
    },

    // 获取报告详情
    async getReport(taskId: string): Promise<ReportDetail> {
        const response = await fetch(`${API_BASE}/api/reports/${taskId}`)
        if (!response.ok) {
            throw new Error('获取报告详情失败')
        }
        return response.json()
    },
}
