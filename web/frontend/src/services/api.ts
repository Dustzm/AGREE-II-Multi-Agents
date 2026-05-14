import { API_URL } from '@/config'
import { apiFetch } from '@/services/http'

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
    is_attachment?: boolean  // 是否为附件文件
    attachment_count?: number
}

export interface InputDirectoryNode {
    name: string
    path: string
    modified: string
    children: InputDirectoryNode[]
    files: PdfFile[]
    pdfs: PdfFile[]
    file_count: number
    pdf_count: number
    selectable_pdf_count: number
}

export interface PdfListResponse {
    tree: InputDirectoryNode
    folders: InputDirectoryNode[]
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

// 结构化推理相关类型
export interface ReasonItem {
    point: string
    explanation: string
    evidence_chunks: string[]  // chunk_id 数组，直接用于原文定位
    evidence_refs?: number[]   // TODO [COMPAT] 兼容旧格式，后续清理
}

export interface StructuredReasoning {
    summary: string
    reasons: ReasonItem[]
    score_justification: string
}

export interface ScoringPrincipleItem {
    range: string
    condition: string
}

export interface ItemEvaluation {
    item_id: string
    domain: number
    title: string
    score: number | null
    step1_locate: string | null
    step2_evidence: string[] | null
    // TODO [COMPAT] 支持新旧两种格式，后续清理
    step3_reasoning: string | StructuredReasoning | null
    evaluated_at: string | null
    scoring_principle: ScoringPrincipleItem[] | null  // 评分准则
    evidence_sources: string[] | null  // chunk_id 列表，用于原文定位
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

// 证据定位相关类型
export interface EvidenceMatch {
    page_idx: number
    bbox: number[]
    text_preview: string
    chunk_id: string | null
}

export interface EvidenceLocationResponse {
    matches: EvidenceMatch[]
    total: number
}

export interface ManualResponse {
    lang: 'zh-CN' | 'en-US'
    available: boolean
    title: string
    content: string
}

export const api = {
    // 列出 input 目录下的 PDF 文件
    async listPdfFiles(): Promise<PdfListResponse> {
        const response = await apiFetch(`${API_BASE}/files/list`)
        if (!response.ok) {
            throw new Error('获取文件列表失败')
        }
        return response.json()
    },

    // 启动评价任务
    async startTask(pdfPath: string): Promise<StartTaskResponse> {
        const response = await apiFetch(`${API_BASE}/files/start`, {
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

        const response = await apiFetch(`${API_BASE}/upload`, {
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

        const response = await apiFetch(`${API_BASE}/files/upload`, {
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
            const enhancedFile = file as File & { webkitRelativePath?: string };
            const relativePath = enhancedFile.webkitRelativePath || file.name
            formData.append('files', file, relativePath)
        })

        const response = await apiFetch(`${API_BASE}/files/upload-folder`, {
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
        const response = await apiFetch(`${API_BASE}/reports${params}`)
        if (!response.ok) {
            throw new Error('获取报告列表失败')
        }
        return response.json()
    },

    // 获取报告详情
    async getReport(taskId: string, folder?: string): Promise<ReportDetail> {
        const params = folder ? `?folder=${encodeURIComponent(folder)}` : ''
        const response = await apiFetch(`${API_BASE}/reports/${taskId}${params}`)
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
        const response = await apiFetch(url)
        if (!response.ok) {
            throw new Error('健康检查失败')
        }
        return response.json()
    },

    // 获取使用手册
    async getManual(lang: 'zh-CN' | 'en-US'): Promise<ManualResponse> {
        const response = await apiFetch(`${API_BASE}/manual?lang=${encodeURIComponent(lang)}`)
        if (!response.ok) {
            throw new Error('获取使用手册失败')
        }
        return response.json()
    },

    // 获取使用手册资源 URL
    getManualAssetUrl(path: string): string {
        return `${API_BASE}/manual-assets/${path
            .split('/')
            .map(segment => encodeURIComponent(segment))
            .join('/')}`
    },

    // 获取证据在 PDF 中的位置
    async getEvidenceLocation(
        taskId: string,
        options: {
            evidenceTexts?: string[]
            chunkIds?: string[]
        },
        folder?: string
    ): Promise<EvidenceLocationResponse> {
        const params = folder ? `?folder=${encodeURIComponent(folder)}` : ''
        const response = await apiFetch(`${API_BASE}/reports/${taskId}/evidence_location${params}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                evidence_texts: options.evidenceTexts,
                chunk_ids: options.chunkIds
            }),
        })
        if (!response.ok) {
            throw new Error('获取证据位置失败')
        }
        return response.json()
    },

    // ===== Benchmark API =====

    // 列出已上传的人类评分文件
    async listHumanFiles(): Promise<{ files: HumanFileInfo[] }> {
        const response = await apiFetch(`${API_BASE}/benchmark/human-files`)
        if (!response.ok) {
            throw new Error('获取人类评分文件列表失败')
        }
        return response.json()
    },

    // 上传人类评分文件
    async uploadHumanFile(file: File): Promise<{ status: string; message: string; file: HumanFileInfo }> {
        const formData = new FormData()
        formData.append('file', file)
        const response = await apiFetch(`${API_BASE}/benchmark/human-files`, {
            method: 'POST',
            body: formData,
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '上传失败' }))
            throw new Error(error.detail || '上传失败')
        }
        return response.json()
    },

    // 删除人类评分文件
    async deleteHumanFile(filename: string): Promise<{ status: string; message: string }> {
        const response = await apiFetch(`${API_BASE}/benchmark/human-files/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
        })
        if (!response.ok) {
            throw new Error('删除文件失败')
        }
        return response.json()
    },

    // 列出可用的 batch 目录
    async listBatches(): Promise<{ batches: BatchInfo[] }> {
        const response = await apiFetch(`${API_BASE}/benchmark/batches`)
        if (!response.ok) {
            throw new Error('获取 batch 列表失败')
        }
        return response.json()
    },

    // 执行 Benchmark
    async runBenchmark(humanFile: string, batchIds: string[], threshold: number = 2): Promise<BenchmarkRunResponse> {
        const response = await apiFetch(`${API_BASE}/benchmark/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                human_file: humanFile,
                batch_ids: batchIds,
                threshold: threshold,
            }),
        })
        if (!response.ok) {
            throw new Error('执行 Benchmark 失败')
        }
        return response.json()
    },

    // 列出 Benchmark 结果
    async listBenchmarkResults(): Promise<{ results: BenchmarkResultInfo[] }> {
        const response = await apiFetch(`${API_BASE}/benchmark/results`)
        if (!response.ok) {
            throw new Error('获取结果列表失败')
        }
        return response.json()
    },

    // 删除 Benchmark 结果
    async deleteBenchmarkResult(filename: string): Promise<{ status: string; message: string; deleted_files: string[] }> {
        const response = await apiFetch(`${API_BASE}/benchmark/results/${encodeURIComponent(filename)}`, {
            method: 'DELETE',
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '删除实验结果失败' }))
            throw new Error(error.detail || '删除实验结果失败')
        }
        return response.json()
    },

    // 获取 Benchmark 结果下载 URL
    getBenchmarkDownloadUrl(filename: string): string {
        return `${API_BASE}/benchmark/download/${encodeURIComponent(filename)}`
    },

    // 获取 Benchmark 结果详情
    async getBenchmarkDetail(filename: string): Promise<BenchmarkDetailResponse> {
        const response = await apiFetch(`${API_BASE}/benchmark/detail/${encodeURIComponent(filename)}`)
        if (!response.ok) {
            throw new Error('获取结果详情失败')
        }
        return response.json()
    },

    // 保存 Benchmark 修改
    async saveBenchmarkChanges(filename: string, updates: ScoreUpdate[]): Promise<{ status: string; message: string }> {
        const response = await apiFetch(`${API_BASE}/benchmark/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename,
                updates
            }),
        })
        if (!response.ok) {
            throw new Error('保存修改失败')
        }
        return response.json()
    },

    // 添加评论
    async addComment(filename: string, guideId: string, content: string): Promise<{ status: string; comment: GuideComment }> {
        const response = await apiFetch(`${API_BASE}/benchmark/comments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename,
                guide_id: guideId,
                content
            }),
        })
        if (!response.ok) {
            throw new Error('添加评论失败')
        }
        return response.json()
    },

    // 更新评论
    async updateComment(filename: string, guideId: string, commentId: string, content: string): Promise<{ status: string; comment: GuideComment }> {
        const response = await apiFetch(`${API_BASE}/benchmark/comments`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename,
                guide_id: guideId,
                comment_id: commentId,
                content
            }),
        })
        if (!response.ok) {
            throw new Error('更新评论失败')
        }
        return response.json()
    },

    // 删除评论
    async deleteComment(filename: string, guideId: string, commentId: string): Promise<{ status: string }> {
        const response = await apiFetch(`${API_BASE}/benchmark/comments`, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filename,
                guide_id: guideId,
                comment_id: commentId
            }),
        })
        if (!response.ok) {
            throw new Error('删除评论失败')
        }
        return response.json()
    },

    // ===== 配置管理 API =====

    // 获取 LLM 配置
    async getLLMConfig(): Promise<LLMConfigResponse> {
        const response = await apiFetch(`${API_BASE}/config/llm`)
        if (!response.ok) {
            throw new Error('获取 LLM 配置失败')
        }
        return response.json()
    },

    // 更新 LLM 配置
    async updateLLMConfig(config: LLMConfigUpdate): Promise<LLMConfigResponse> {
        const response = await apiFetch(`${API_BASE}/config/llm`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config),
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: '更新失败' }))
            throw new Error(error.detail || '更新 LLM 配置失败')
        }
        return response.json()
    },

    // 测试 LLM 连接
    async testLLMConnection(config?: LLMConfigUpdate): Promise<ConnectionTestResult> {
        const response = await apiFetch(`${API_BASE}/config/llm/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config ?? {}),
        })
        if (!response.ok) {
            throw new Error('测试连接失败')
        }
        return response.json()
    },

    // LLM 健康验证
    async checkLLMHealth(config?: LLMConfigUpdate): Promise<HealthCheckResult> {
        const response = await apiFetch(`${API_BASE}/config/llm/health`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config ?? {}),
        })
        if (!response.ok) {
            throw new Error('健康验证失败')
        }
        return response.json()
    },

    // 获取提供商预设列表
    async getProviders(): Promise<Record<string, ProviderInfo>> {
        const response = await apiFetch(`${API_BASE}/config/providers`)
        if (!response.ok) {
            throw new Error('获取提供商列表失败')
        }
        return response.json()
    },
}

// ===== Benchmark 类型定义 =====

export interface ScoreUpdate {
    guide_id: string
    item_id: string
    field: string
    value: number
}

export interface HumanFileInfo {
    name: string
    path: string
    size: number
    modified: string
}

export interface BatchInfo {
    name: string
    path: string
    task_count: number
    created: string
}

export interface GuideConsistency {
    seq_no: number
    guide_id: string
    guide_name: string
    consistent_count: number
    total_matches: number
    consistency: number
    status: string
}

export interface ItemConsistency {
    item_id: string
    item_name: string
    consistent_count: number
    total_matches: number
    consistency: number
}

export interface BenchmarkSummary {
    total_guides: number
    success_count: number
    overall_consistency: number
    guide_consistencies: GuideConsistency[]
    item_consistencies: ItemConsistency[]
    guide_details: GuideDetail[]  // 指南详情列表
    rater_names: string[]  // 评分者姓名列表
}

export interface BenchmarkResultInfo {
    name: string
    path: string
    size: number
    created: string
    human_file?: string
    batch_ids?: string[]
}

export interface BenchmarkRunResponse {
    status: string
    message: string
    xlsx_file?: string
    download_url?: string
    summary?: BenchmarkSummary
    error?: string
}

// 新增类型定义
export interface GuideItemDetail {
    item_id: string
    item_name: string
    human_score1: number | null
    human_score2: number | null
    batch1_model_score: number | null
    batch2_model_score: number | null
    // 原始值字段（用于标记修改状态）
    human_score1_original?: number | null
    human_score2_original?: number | null
    batch1_model_score_original?: number | null
    batch2_model_score_original?: number | null
}

export interface GuideDetail {
    seq_no: number
    guide_id: string
    guide_name: string
    status: string
    consistency: number
    items: GuideItemDetail[]  // 23个条目
    comments: GuideComment[]  // 备注/评论列表
}

export interface GuideComment {
    id: string
    author: string
    content: string
    created_at: string
    updated_at?: string
}

export interface BenchmarkDetailResponse {
    xlsx_file: string
    human_file: string
    batch_ids: string[]
    threshold: number
    created: string
    summary: BenchmarkSummary
}

// ===== 配置管理 类型定义 =====

export interface LLMConfigResponse {
    provider: string
    base_url: string
    api_key_masked: string
    model_name: string
    timeout: number
    is_vision_model: boolean
}

export interface LLMConfigUpdate {
    provider?: string
    base_url?: string
    api_key?: string
    model_name?: string
    timeout?: number
    is_vision_model?: boolean
}

export interface ProviderModelInfo {
    id: string
    vision: boolean
}

export interface ProviderInfo {
    name: string
    prefix: string
    default_base_url: string
    need_base_url: boolean
    env_key: string
    env_base?: string
    models: ProviderModelInfo[]
}

export interface ConnectionTestResult {
    success: boolean
    message: string
    latency_ms?: number
    models?: string[]
}

export interface HealthCheckResult {
    success: boolean
    message: string
    latency_ms?: number
    model_name?: string
    reply?: string
}
