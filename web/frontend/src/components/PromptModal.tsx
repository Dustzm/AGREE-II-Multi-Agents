/**
 * PromptModal - 提示词详情弹窗
 * 
 * 显示 Appraiser 评价时使用的完整提示词
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
    Dialog,
    DialogContent,
    DialogTitle,
} from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import { Copy, Check } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface PromptModalProps {
    isOpen: boolean
    onClose: () => void
    skillId?: string
    prompt?: string
}

export function PromptModal({ isOpen, onClose, skillId, prompt }: PromptModalProps) {
    const { t } = useTranslation()
    const [copied, setCopied] = useState(false)

    const handleCopy = async () => {
        if (prompt) {
            await navigator.clipboard.writeText(prompt)
            setCopied(true)
            setTimeout(() => setCopied(false), 2000)
        }
    }

    return (
        <Dialog open={isOpen} onOpenChange={onClose}>
            <DialogContent className="max-w-4xl max-h-[85vh] flex flex-col p-0 gap-0 bg-white border border-gray-200/80 shadow-2xl sm:rounded-xl overflow-hidden ring-1 ring-black/5">
                <div className="flex items-center px-6 py-4 border-b border-gray-200/50 bg-gray-50/80">
                    <div className="flex items-center gap-3">
                        <DialogTitle className="text-base font-semibold text-gray-900">{t('prompt.title')}</DialogTitle>
                        {skillId && (
                            <Badge variant="secondary" className="font-mono text-xs font-medium text-gray-600 bg-white/80 border border-gray-200/50">
                                {skillId}
                            </Badge>
                        )}
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={handleCopy}
                            className="h-6 w-6 rounded-full text-gray-400 hover:text-gray-900 hover:bg-black/5 transition-colors"
                            title={t('prompt.copy')}
                        >
                            {copied ? (
                                <Check className="h-3.5 w-3.5 text-emerald-600" />
                            ) : (
                                <Copy className="h-3.5 w-3.5" />
                            )}
                        </Button>
                    </div>
                </div>

                {/* 滚动区域：使用 GPU 加速和滚动隔离防止抖动 */}
                <div
                    className="flex-1 w-full overflow-y-auto bg-white scrollbar-thin scrollbar-thumb-gray-300/50 scrollbar-track-transparent hover:scrollbar-thumb-gray-400/80"
                    style={{
                        overscrollBehavior: 'contain',
                        willChange: 'scroll-position',
                        transform: 'translateZ(0)',
                        isolation: 'isolate',
                    }}
                >
                    <div className="p-6">
                        <div className="prose prose-sm max-w-none text-gray-900 prose-headings:font-semibold prose-h1:text-xl prose-h2:text-lg prose-p:leading-relaxed prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-200 prose-pre:text-gray-800 prose-table:border-collapse prose-th:border prose-th:border-gray-200 prose-th:p-2 prose-th:bg-gray-50 prose-td:border prose-td:border-gray-200 prose-td:p-2">
                            {prompt ? (
                                <ReactMarkdown remarkPlugins={[remarkGfm]}>{prompt}</ReactMarkdown>
                            ) : (
                                <p className="text-muted-foreground italic">{t('prompt.empty')}</p>
                            )}
                        </div>
                    </div>
                </div>
            </DialogContent>
        </Dialog>
    )
}

