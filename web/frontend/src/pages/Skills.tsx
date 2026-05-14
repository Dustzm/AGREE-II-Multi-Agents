import { useEffect, useState, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { skillsService, Skill, SkillUpdateDetails } from '../services/skills'
import { motion, AnimatePresence } from 'framer-motion'
import { Layers, Check, X, AlertCircle, Pencil, Loader2, ToggleLeft, ToggleRight } from 'lucide-react'
import { cn } from '@/utils/cn'
import { useAuth } from '@/auth/useAuth'
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog"
import { Button } from '@/components/ui/button'

export function Skills() {
    const { t, i18n } = useTranslation()
    const { isAdmin } = useAuth()
    const [skills, setSkills] = useState<Skill[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<string | null>(null)
    const [batchUpdating, setBatchUpdating] = useState(false)

    // Edit State
    const [editingSkill, setEditingSkill] = useState<Skill | null>(null)
    const [isDialogOpen, setIsDialogOpen] = useState(false)
    const [editForm, setEditForm] = useState<SkillUpdateDetails>({
        title_zh: '',
        description_zh: '',
        locating_info_zh: '',
        title: '',
        description: '',
        locating_info: '',
        criteria: [],
        scoring_principle: []
    })
    const [criteriaText, setCriteriaText] = useState('')

    // Auto-save State
    const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle')
    const skipSaveRef = useRef(false)

    useEffect(() => {
        loadSkills()
    }, [i18n.language])  // 语言变化时重新加载数据

    // Auto-hide 'saved' status after 3 seconds
    useEffect(() => {
        if (saveStatus === 'saved') {
            const timer = setTimeout(() => {
                setSaveStatus('idle')
            }, 3000)
            return () => clearTimeout(timer)
        }
    }, [saveStatus])

    // Debounced Auto-save trigger
    useEffect(() => {
        if (!editingSkill || !isDialogOpen) return

        // Skip the initial effect trigger when opening dialog
        if (skipSaveRef.current) {
            skipSaveRef.current = false
            return
        }

        // Only set to saving if we are not already saving or in error
        if (saveStatus !== 'saving') {
            setSaveStatus('saving')
        }

        const timer = setTimeout(() => {
            performSave()
        }, 1000)

        return () => clearTimeout(timer)
    }, [editForm, criteriaText])

    const loadSkills = async () => {
        try {
            const data = await skillsService.getAll()
            setSkills(data)
        } catch (err) {
            setError('加载配置失败')
            console.error(err)
        } finally {
            setLoading(false)
        }
    }

    const toggleSkill = async (id: string, currentStatus: boolean) => {
        try {
            const updatedSkill = await skillsService.updateStatus(id, !currentStatus)
            setSkills(prev => prev.map(s => s.item_id === id ? updatedSkill : s))
        } catch (err) {
            console.error('Update failed', err)
        }
    }

    // 批量更新所有条目状态
    const batchToggle = async (enabled: boolean) => {
        setBatchUpdating(true)
        try {
            const updatedSkills = await skillsService.batchUpdateStatus(enabled)
            setSkills(updatedSkills)
        } catch (err) {
            console.error('Batch update failed', err)
        } finally {
            setBatchUpdating(false)
        }
    }

    const openEditDialog = (skill: Skill) => {
        skipSaveRef.current = true // Prevent auto-save on open
        setEditingSkill(skill)
        setEditForm({
            title_zh: skill.title_zh || skill.title,
            description_zh: skill.description_zh || skill.description,
            locating_info_zh: skill.locating_info_zh || skill.locating_info,
            title: skill.title_en || '',
            description: skill.description_en || '',
            locating_info: skill.locating_info_en || '',
            criteria: skill.criteria || [],
            scoring_principle: skill.scoring_principle || []
        })
        setCriteriaText((skill.criteria || []).join('\n'))
        setIsDialogOpen(true)
        setSaveStatus('idle')
    }



    const performSave = async () => {
        if (!editingSkill) return

        try {
            const criteriaList = criteriaText.split('\n').filter(line => line.trim() !== '')
            const payload: SkillUpdateDetails = {
                ...editForm,
                criteria: criteriaList
            }

            // Silent update
            await skillsService.updateDetails(editingSkill.item_id, payload)

            // Reload skills to ensure data consistency
            await loadSkills()

            setSaveStatus('saved')
        } catch (err) {
            console.error('Auto-save failed', err)
            setSaveStatus('error')
        }
    }

    const handleScoringChange = (index: number, field: string, value: string) => {
        const newPrinciples = [...editForm.scoring_principle]
        newPrinciples[index] = { ...newPrinciples[index], [field]: value }
        setEditForm({ ...editForm, scoring_principle: newPrinciples })
    }

    const domains = Array.from(new Set(skills.map(s => s.domain))).sort()

    if (loading) return (
        <div className="flex h-full items-center justify-center p-8 bg-gray-50/50">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500/80"></div>
        </div>
    )

    if (error) return (
        <div className="flex h-full items-center justify-center text-red-500 gap-2 p-8">
            <AlertCircle className="h-5 w-5" />
            <span>{error}</span>
        </div>
    )

    return (
        <div className="h-full w-full overflow-y-auto bg-[#f5f5f7] p-8 space-y-10" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
            <header className="mb-12 relative z-10">
                <div className="flex items-start justify-between">
                    <div>
                        <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.22em] text-black/36">
                            Configuration surface
                        </div>
                        <h2 className="mb-2 text-4xl font-semibold tracking-[-0.045em] text-[#1d1d1f]">
                            {t('skills.title')}
                        </h2>
                        <p className="max-w-2xl text-[1.02rem] leading-[1.55] text-black/58">
                            {t('skills.description')}
                        </p>
                    </div>
                    {isAdmin && (
                    <div className="flex items-center gap-3">
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => batchToggle(true)}
                            disabled={batchUpdating}
                            className="gap-2 rounded-full border-black/10 bg-white text-[#1d1d1f] hover:border-[#0071e3]/22 hover:bg-[#0071e3]/6"
                        >
                            {batchUpdating ? <Loader2 className="h-4 w-4 animate-spin" /> : <ToggleRight className="h-4 w-4" />}
                            {t('skills.button.enableAll')}
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => batchToggle(false)}
                            disabled={batchUpdating}
                            className="gap-2 rounded-full border-black/10 bg-white text-black/66 hover:border-[#0071e3]/22 hover:bg-[#0071e3]/6 hover:text-[#1d1d1f]"
                        >
                            {batchUpdating ? <Loader2 className="h-4 w-4 animate-spin" /> : <ToggleLeft className="h-4 w-4" />}
                            {t('skills.button.disableAll')}
                        </Button>
                    </div>
                    )}
                </div>
            </header>

            {domains.map(domain => {
                const domainSkills = skills.filter(s => s.domain === domain)
                const domainName = domainSkills[0]?.domain_name || `Domain ${domain}`

                return (
                    <section key={domain} className="space-y-6">
                        <div className="flex items-center gap-3 border-b border-black/8 pb-3">
                            <div className="rounded-2xl border border-black/6 bg-white p-2 shadow-[0_16px_32px_-26px_rgba(0,0,0,0.25)]">
                                <Layers className="h-5 w-5 text-[#0071e3]" />
                            </div>
                            <h3 className="text-2xl font-semibold tracking-[-0.035em] text-[#1d1d1f]">
                                <span className="mr-2 text-[#0071e3]">#{domain}</span>
                                {domainName}
                            </h3>
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                            {domainSkills.map((skill) => (
                                <SkillCard
                                    key={skill.item_id}
                                    skill={skill}
                                    onToggle={() => toggleSkill(skill.item_id, skill.enabled)}
                                    onEdit={() => openEditDialog(skill)}
                                    isAdmin={isAdmin}
                                />
                            ))}
                        </div>
                    </section>
                )
            })}

            {/* Edit Dialog */}
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent className="sm:max-w-[800px] max-h-[85vh] overflow-hidden flex flex-col bg-white/95 border-white/20 shadow-2xl">
                    <DialogHeader className="flex flex-row items-center justify-between mr-8 sticky top-0 bg-white/95 z-10 pb-4 border-b border-gray-100">
                        <DialogTitle>{t('skills.dialog.title')} ({editingSkill?.item_id})</DialogTitle>

                        <div className="h-6"> {/* Fixed height to prevent layout shift */}
                            <AnimatePresence mode="wait">
                                {saveStatus === 'saving' && (
                                    <motion.div
                                        key="saving"
                                        initial={{ opacity: 0, y: -10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: 10 }}
                                        className="flex items-center gap-2 px-3 py-1 rounded-full bg-blue-50 border border-blue-200/50 text-xs font-medium text-blue-600 shadow-sm will-change-transform"
                                    >
                                        <Loader2 className="w-3 h-3 animate-spin" />
                                        <span>{t('skills.dialog.saving')}</span>
                                    </motion.div>
                                )}
                                {saveStatus === 'saved' && (
                                    <motion.div
                                        key="saved"
                                        initial={{ opacity: 0, y: -10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: 10 }}
                                        className="flex items-center gap-2 px-3 py-1 rounded-full bg-green-50 border border-green-200/50 text-xs font-medium text-green-600 shadow-sm will-change-transform"
                                    >
                                        <Check className="w-3 h-3" />
                                        <span>{t('skills.dialog.saved')}</span>
                                    </motion.div>
                                )}
                                {saveStatus === 'error' && (
                                    <motion.div
                                        key="error"
                                        initial={{ opacity: 0, y: -10 }}
                                        animate={{ opacity: 1, y: 0 }}
                                        exit={{ opacity: 0, y: 10 }}
                                        className="flex items-center gap-2 px-3 py-1 rounded-full bg-red-50 border border-red-200/50 text-xs font-medium text-red-600 shadow-sm will-change-transform"
                                    >
                                        <AlertCircle className="w-3 h-3" />
                                        <span>{t('skills.dialog.saveFailed')}</span>
                                    </motion.div>
                                )}
                            </AnimatePresence>
                        </div>
                    </DialogHeader>

                    <div className="grid gap-6 py-4 overflow-y-auto flex-1">
                        {/* Title Group */}
                        <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium leading-none text-gray-500">
                                    {t('skills.form.titleEn')}
                                </label>
                                <input
                                    className="flex h-9 w-full rounded-md border border-input bg-white/50 px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    value={editForm.title}
                                    onChange={(e) => setEditForm({ ...editForm, title: e.target.value })}
                                />
                            </div>
                            <div className="grid gap-2">
                                <label className="text-sm font-medium leading-none text-blue-600">
                                    {t('skills.form.titleZh')}
                                </label>
                                <input
                                    className="flex h-9 w-full rounded-md border border-input bg-white/50 px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    value={editForm.title_zh}
                                    onChange={(e) => setEditForm({ ...editForm, title_zh: e.target.value })}
                                />
                            </div>
                        </div>

                        {/* Description Group */}
                        <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium leading-none text-gray-500">
                                    {t('skills.form.descEn')}
                                </label>
                                <textarea
                                    className="flex min-h-[80px] w-full rounded-md border border-input bg-white/50 px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    value={editForm.description}
                                    onChange={(e) => setEditForm({ ...editForm, description: e.target.value })}
                                />
                            </div>
                            <div className="grid gap-2">
                                <label className="text-sm font-medium leading-none text-blue-600">
                                    {t('skills.form.descZh')}
                                </label>
                                <textarea
                                    className="flex min-h-[80px] w-full rounded-md border border-input bg-white/50 px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    value={editForm.description_zh}
                                    onChange={(e) => setEditForm({ ...editForm, description_zh: e.target.value })}
                                />
                            </div>
                        </div>

                        {/* Locating Info Group */}
                        <div className="grid grid-cols-2 gap-4">
                            <div className="grid gap-2">
                                <label className="text-sm font-medium leading-none text-gray-500">
                                    {t('skills.form.locatingEn')}
                                </label>
                                <input
                                    className="flex h-9 w-full rounded-md border border-input bg-white/50 px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    value={editForm.locating_info}
                                    onChange={(e) => setEditForm({ ...editForm, locating_info: e.target.value })}
                                />
                            </div>
                            <div className="grid gap-2">
                                <label className="text-sm font-medium leading-none text-blue-600">
                                    {t('skills.form.locatingZh')}
                                </label>
                                <input
                                    className="flex h-9 w-full rounded-md border border-input bg-white/50 px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                                    value={editForm.locating_info_zh}
                                    onChange={(e) => setEditForm({ ...editForm, locating_info_zh: e.target.value })}
                                />
                            </div>
                        </div>

                        {/* Scoring Principle Table */}
                        <div className="grid gap-2">
                            <div className="flex items-center justify-between">
                                <label className="text-sm font-medium leading-none text-gray-700">
                                    {t('skills.form.scoringPrinciple')}
                                </label>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="sm"
                                    onClick={() => {
                                        const newPrinciples = [...editForm.scoring_principle, { range: '', condition: '', condition_zh: '' }]
                                        setEditForm({ ...editForm, scoring_principle: newPrinciples })
                                    }}
                                    className="h-7 text-xs gap-1"
                                >
                                    <span>{t('skills.form.addRule')}</span>
                                </Button>
                            </div>
                            <div className="rounded-md border border-gray-200 overflow-hidden">
                                <table className="w-full text-sm text-left">
                                    <thead className="bg-gray-50 text-gray-500 font-medium border-b border-gray-200">
                                        <tr>
                                            <th className="p-3 w-24 text-center">{t('skills.form.range')}</th>
                                            <th className="p-3">{t('skills.form.conditionEn')}</th>
                                            <th className="p-3 text-blue-600">{t('skills.form.conditionZh')}</th>
                                            <th className="p-3 w-12 text-center">{t('skills.form.actions')}</th>
                                        </tr>
                                    </thead>
                                    <tbody className="divide-y divide-gray-100 bg-white/50">
                                        {editForm.scoring_principle.map((item, index) => (
                                            <tr key={index} className="group hover:bg-white/80 transition-colors">
                                                <td className="p-2">
                                                    <input
                                                        className="w-full bg-white/70 border border-gray-200 rounded px-2 py-1 text-center font-mono text-xs focus:ring-1 focus:ring-blue-400 focus:border-blue-400"
                                                        value={item.range || ''}
                                                        onChange={(e) => handleScoringChange(index, 'range', e.target.value)}
                                                        placeholder="1-7"
                                                    />
                                                </td>
                                                <td className="p-2">
                                                    <input
                                                        className="w-full bg-transparent border-none p-1 focus:ring-0 text-sm placeholder:text-gray-300"
                                                        value={item.condition || ''}
                                                        onChange={(e) => handleScoringChange(index, 'condition', e.target.value)}
                                                        placeholder="English condition"
                                                    />
                                                </td>
                                                <td className="p-2">
                                                    <input
                                                        className="w-full bg-transparent border-none p-1 focus:ring-0 text-sm placeholder:text-gray-300"
                                                        value={item.condition_zh || ''}
                                                        onChange={(e) => handleScoringChange(index, 'condition_zh', e.target.value)}
                                                        placeholder="中文条件"
                                                    />
                                                </td>
                                                <td className="p-2 text-center">
                                                    <Button
                                                        type="button"
                                                        variant="ghost"
                                                        size="icon"
                                                        onClick={() => {
                                                            const newPrinciples = editForm.scoring_principle.filter((_, i) => i !== index)
                                                            setEditForm({ ...editForm, scoring_principle: newPrinciples })
                                                        }}
                                                        className="h-6 w-6 text-gray-400 hover:text-red-500 hover:bg-red-50"
                                                        title={t('skills.form.deleteRow')}
                                                    >
                                                        <X className="h-3.5 w-3.5" />
                                                    </Button>
                                                </td>
                                            </tr>
                                        ))}
                                        {editForm.scoring_principle.length === 0 && (
                                            <tr>
                                                <td colSpan={4} className="p-4 text-center text-gray-400 text-sm">
                                                    {t('skills.form.noRules')}
                                                </td>
                                            </tr>
                                        )}
                                    </tbody>
                                </table>
                            </div>
                        </div>

                        <div className="grid gap-2">
                            <label className="text-sm font-medium leading-none text-gray-700">
                                {t('skills.form.criteria')}
                            </label>
                            <textarea
                                className="flex min-h-[120px] w-full rounded-md border border-input bg-white/50 px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring font-mono text-xs"
                                value={criteriaText}
                                onChange={(e) => setCriteriaText(e.target.value)}
                            />
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}

function SkillCard({ skill, onToggle, onEdit, isAdmin }: { skill: Skill, onToggle: () => void, onEdit: () => void, isAdmin: boolean }) {
    const { t } = useTranslation()

    return (
        <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            className={cn(
                "group relative overflow-hidden rounded-2xl border transition-all duration-300",
                "bg-white shadow-sm hover:shadow-xl hover:-translate-y-1",
                skill.enabled
                    ? "border-blue-100/50"
                    : "border-gray-200/50 bg-gray-50/50 opacity-80 grayscale-[0.5]"
            )}
        >
            <div className="absolute inset-0 bg-gradient-to-br from-white/40 to-white/10 pointer-events-none" />

            {/* Status Indicator Stripe */}
            <div className={cn(
                "absolute top-0 left-0 right-0 h-1 transition-colors duration-300",
                skill.enabled ? "bg-gradient-to-r from-blue-500 to-indigo-500" : "bg-gray-300"
            )} />

            <div className="relative p-6 flex flex-col h-full">
                <div className="flex justify-between items-start gap-4 mb-4">
                    <div className="flex-1">
                        <span className="inline-block px-2 py-1 rounded-md bg-gray-100/80 text-xs font-mono text-gray-500 mb-2 border border-gray-200/50">
                            {skill.item_id}
                        </span>
                        <h4 className={cn(
                            "text-lg font-bold leading-tight transition-colors",
                            skill.enabled ? "text-gray-900" : "text-gray-500"
                        )}>
                            {skill.title}
                        </h4>
                    </div>

                    <div className="flex items-center gap-2">
                        {isAdmin && (
                        <Button
                            variant="ghost"
                            size="icon"
                            onClick={onEdit}
                            className="hidden group-hover:flex h-8 w-8 text-gray-400 hover:text-blue-600 hover:bg-blue-50"
                            title="编辑条目"
                        >
                            <Pencil className="h-4 w-4" />
                        </Button>
                        )}

                        {isAdmin ? (
                        <button
                            onClick={onToggle}
                            className={cn(
                                "relative inline-flex h-7 w-12 flex-shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2",
                                skill.enabled ? 'bg-blue-600' : 'bg-gray-300'
                            )}
                            title={skill.enabled ? t('skills.card.clickToDisable') : t('skills.card.clickToEnable')}
                        >
                            <span className="sr-only">Toggle setting</span>
                            <span
                                aria-hidden="true"
                                className={cn(
                                    "pointer-events-none inline-block h-6 w-6 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out",
                                    skill.enabled ? 'translate-x-5' : 'translate-x-0'
                                )}
                            />
                        </button>
                        ) : (
                        <span className={cn(
                            "inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium",
                            skill.enabled ? 'bg-green-50 text-green-600 border border-green-200/60' : 'bg-gray-100 text-gray-400 border border-gray-200/60'
                        )}>
                            {skill.enabled ? t('skills.card.enabled') : t('skills.card.disabled')}
                        </span>
                        )}
                    </div>
                </div>

                <p className="text-sm text-gray-600 flex-1 leading-relaxed mb-4 line-clamp-3">
                    {skill.description}
                </p>

                <div className="mt-auto pt-4 border-t border-gray-100/50 flex items-center justify-between text-xs text-gray-400">
                    <div className="flex items-center gap-1.5">
                        {skill.enabled ? (
                            <div className="flex items-center text-green-600 font-medium">
                                <Check className="w-3.5 h-3.5 mr-1" />
                                {t('skills.card.enabled')}
                            </div>
                        ) : (
                            <div className="flex items-center text-gray-500">
                                <X className="w-3.5 h-3.5 mr-1" />
                                {t('skills.card.disabled')}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </motion.div>
    )
}
