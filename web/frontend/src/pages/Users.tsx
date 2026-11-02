import { notifyError, notifySuccess } from '@/i18n/notify'
import { MessageView } from '@/components/MessageView'
import { asMessage, type UiMessage } from '@/i18n/messages'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { motion, AnimatePresence } from 'framer-motion'
import {
    Users as UsersIcon,
    Plus,
    Pencil,
    Trash2,
    Shield,
    User as UserIcon,
    Check,
    X,
    Loader2,
    AlertCircle,
    Eye,
    EyeOff,
} from 'lucide-react'
import { cn } from '@/utils/cn'
import { usersService, type User, type CreateUserPayload, type UpdateUserPayload } from '@/services/users'
import { useAuth } from '@/auth/useAuth'
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

export function Users() {
    const { t } = useTranslation()
    const { user: currentUser } = useAuth()

    const [users, setUsers] = useState<User[]>([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState<UiMessage | null>(null)

    // 创建对话框
    const [isCreateOpen, setIsCreateOpen] = useState(false)
    const [createForm, setCreateForm] = useState<CreateUserPayload>({
        username: '',
        password: '',
        display_name: '',
        role: 'user',
    })
    const [creating, setCreating] = useState(false)
    const [showCreatePassword, setShowCreatePassword] = useState(false)

    // 编辑对话框
    const [editingUser, setEditingUser] = useState<User | null>(null)
    const [isEditOpen, setIsEditOpen] = useState(false)
    const [editForm, setEditForm] = useState<UpdateUserPayload>({})
    const [editPassword, setEditPassword] = useState('')
    const [showEditPassword, setShowEditPassword] = useState(false)
    const [updating, setUpdating] = useState(false)

    // 删除确认
    const [deletingUsername, setDeletingUsername] = useState<string | null>(null)

    useEffect(() => {
        loadUsers()
    }, [])

    const loadUsers = async () => {
        try {
            setLoading(true)
            const data = await usersService.getAll()
            setUsers(data)
            setError(null)
        } catch (err) {
            setError(asMessage(err, 'users.error.loadFailed'))
            console.error(err)
        } finally {
            setLoading(false)
        }
    }

    const handleCreate = async () => {
        if (!createForm.username.trim() || !createForm.password) {
            notifyError({ key: 'users.error.usernameRequired' })
            return
        }
        setCreating(true)
        try {
            await usersService.create(createForm)
            notifySuccess('users.toast.created', { username: createForm.username })
            setIsCreateOpen(false)
            setCreateForm({ username: '', password: '', display_name: '', role: 'user' })
            setShowCreatePassword(false)
            await loadUsers()
        } catch (err: unknown) {
            notifyError(err, 'users.toast.createFailed')
        } finally {
            setCreating(false)
        }
    }

    const openEdit = (user: User) => {
        setEditingUser(user)
        setEditForm({
            display_name: user.display_name,
            role: user.role,
            enabled: user.enabled,
        })
        setEditPassword('')
        setShowEditPassword(false)
        setIsEditOpen(true)
    }

    const handleUpdate = async () => {
        if (!editingUser) return
        setUpdating(true)
        try {
            const payload: UpdateUserPayload = { ...editForm }
            if (editPassword.trim()) {
                payload.password = editPassword
            }
            await usersService.update(editingUser.username, payload)
            notifySuccess('users.toast.updated', { username: editingUser.username })
            setIsEditOpen(false)
            await loadUsers()
        } catch (err: unknown) {
            notifyError(err, 'users.toast.updateFailed')
        } finally {
            setUpdating(false)
        }
    }

    const handleDelete = async (username: string) => {
        try {
            await usersService.delete(username)
            notifySuccess('users.toast.deleted', { username })
            setDeletingUsername(null)
            await loadUsers()
        } catch (err: unknown) {
            notifyError(err, 'users.toast.deleteFailed')
        }
    }

    if (loading) return (
        <div className="flex h-full items-center justify-center p-8 bg-gray-50/50">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500/80"></div>
        </div>
    )

    if (error) return (
        <div className="flex h-full items-center justify-center text-red-500 gap-2 p-8">
            <AlertCircle className="h-5 w-5" />
            <MessageView message={error} />
        </div>
    )

    return (
        <div className="h-full w-full overflow-y-auto bg-[#f5f5f7] p-8 space-y-8" style={{ overscrollBehavior: 'contain', willChange: 'scroll-position' }}>
            {/* 页面标题 */}
            <header className="relative z-10">
                <div className="flex items-start justify-between">
                    <div>
                        <div className="mb-3 text-[0.72rem] font-medium uppercase tracking-[0.22em] text-black/36">
                            {t('users.subtitle')}
                        </div>
                        <h2 className="mb-2 text-4xl font-semibold tracking-[-0.045em] text-[#1d1d1f]">
                            {t('users.title')}
                        </h2>
                        <p className="max-w-2xl text-[1.02rem] leading-[1.55] text-black/58">
                            {t('users.description')}
                        </p>
                    </div>
                    <Button
                        onClick={() => {
                            setCreateForm({ username: '', password: '', display_name: '', role: 'user' })
                            setShowCreatePassword(false)
                            setIsCreateOpen(true)
                        }}
                        className="gap-2 rounded-full bg-[#0071e3] text-white hover:bg-[#0077ED] shadow-sm"
                    >
                        <Plus className="h-4 w-4" />
                        {t('users.addUser')}
                    </Button>
                </div>
            </header>

            {/* 用户列表 */}
            <div className="rounded-2xl border border-black/6 bg-white shadow-sm overflow-hidden">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-gray-100 bg-gray-50/60">
                            <th className="px-6 py-4 text-left font-medium text-gray-500 tracking-wide text-xs uppercase">{t('users.table.username')}</th>
                            <th className="px-6 py-4 text-left font-medium text-gray-500 tracking-wide text-xs uppercase">{t('users.table.displayName')}</th>
                            <th className="px-6 py-4 text-left font-medium text-gray-500 tracking-wide text-xs uppercase">{t('users.table.role')}</th>
                            <th className="px-6 py-4 text-left font-medium text-gray-500 tracking-wide text-xs uppercase">{t('users.table.status')}</th>
                            <th className="px-6 py-4 text-left font-medium text-gray-500 tracking-wide text-xs uppercase">{t('users.table.createdAt')}</th>
                            <th className="px-6 py-4 text-right font-medium text-gray-500 tracking-wide text-xs uppercase">{t('users.table.actions')}</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-50">
                        <AnimatePresence>
                            {users.map((u) => (
                                <motion.tr
                                    key={u.username}
                                    initial={{ opacity: 0 }}
                                    animate={{ opacity: 1 }}
                                    exit={{ opacity: 0 }}
                                    className="group hover:bg-gray-50/50 transition-colors"
                                >
                                    <td className="px-6 py-4">
                                        <div className="flex items-center gap-3">
                                            <div className={cn(
                                                'flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold',
                                                u.role === 'admin'
                                                    ? 'bg-gradient-to-br from-amber-400 to-orange-500 text-white'
                                                    : 'bg-gradient-to-br from-blue-400 to-indigo-500 text-white'
                                            )}>
                                                {u.username[0]?.toUpperCase()}
                                            </div>
                                            <span className="font-medium text-gray-900">{u.username}</span>
                                            {u.username === currentUser?.username && (
                                                <span className="px-1.5 py-0.5 text-[10px] font-medium rounded-full bg-blue-50 text-blue-600 border border-blue-100">
                                                    {t('users.badge.current')}
                                                </span>
                                            )}
                                        </div>
                                    </td>
                                    <td className="px-6 py-4 text-gray-600">
                                        {u.display_name || <span className="text-gray-300">—</span>}
                                    </td>
                                    <td className="px-6 py-4">
                                        {u.role === 'admin' ? (
                                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-amber-50 text-amber-700 border border-amber-200/60">
                                                <Shield className="h-3 w-3" />
                                                {t('users.role.admin')}
                                            </span>
                                        ) : (
                                            <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-medium bg-blue-50 text-blue-600 border border-blue-200/60">
                                                <UserIcon className="h-3 w-3" />
                                                {t('users.role.user')}
                                            </span>
                                        )}
                                    </td>
                                    <td className="px-6 py-4">
                                        {u.enabled ? (
                                            <span className="inline-flex items-center gap-1 text-xs font-medium text-green-600">
                                                <Check className="h-3.5 w-3.5" />
                                                {t('users.status.enabled')}
                                            </span>
                                        ) : (
                                            <span className="inline-flex items-center gap-1 text-xs font-medium text-gray-400">
                                                <X className="h-3.5 w-3.5" />
                                                {t('users.status.disabled')}
                                            </span>
                                        )}
                                    </td>
                                    <td className="px-6 py-4 text-gray-400 text-xs">
                                        {u.created_at?.replace('T', ' ').slice(0, 16) || '—'}
                                    </td>
                                    <td className="px-6 py-4">
                                        <div className="flex items-center justify-end gap-1">
                                            <Button
                                                variant="ghost"
                                                size="icon"
                                                className="h-8 w-8 text-gray-400 hover:text-blue-600 hover:bg-blue-50"
                                                onClick={() => openEdit(u)}
                                            >
                                                <Pencil className="h-3.5 w-3.5" />
                                            </Button>
                                            {u.username !== currentUser?.username && (
                                                <>
                                                    {deletingUsername === u.username ? (
                                                        <div className="flex items-center gap-1">
                                                            <Button
                                                                variant="ghost"
                                                                size="icon"
                                                                className="h-8 w-8 text-red-600 hover:bg-red-50"
                                                                onClick={() => handleDelete(u.username)}
                                                                title={t('users.confirm.delete')}
                                                            >
                                                                <Check className="h-3.5 w-3.5" />
                                                            </Button>
                                                            <Button
                                                                variant="ghost"
                                                                size="icon"
                                                                className="h-8 w-8 text-gray-400 hover:bg-gray-100"
                                                                onClick={() => setDeletingUsername(null)}
                                                                title={t('users.confirm.cancel')}
                                                            >
                                                                <X className="h-3.5 w-3.5" />
                                                            </Button>
                                                        </div>
                                                    ) : (
                                                        <Button
                                                            variant="ghost"
                                                            size="icon"
                                                            className="h-8 w-8 text-gray-400 hover:text-red-600 hover:bg-red-50"
                                                            onClick={() => setDeletingUsername(u.username)}
                                                        >
                                                            <Trash2 className="h-3.5 w-3.5" />
                                                        </Button>
                                                    )}
                                                </>
                                            )}
                                        </div>
                                    </td>
                                </motion.tr>
                            ))}
                        </AnimatePresence>
                    </tbody>
                </table>

                {users.length === 0 && (
                    <div className="py-16 text-center text-gray-400">
                        <UsersIcon className="mx-auto h-10 w-10 mb-3 text-gray-300" />
                        <p>{t('users.empty')}</p>
                    </div>
                )}
            </div>

            {/* 创建用户对话框 */}
            <Dialog open={isCreateOpen} onOpenChange={setIsCreateOpen}>
                <DialogContent className="sm:max-w-[440px] bg-white/95 border-white/20 shadow-2xl">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Plus className="h-5 w-5 text-[#0071e3]" />
                            {t('users.dialog.createTitle')}
                        </DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-5 py-4">
                        <div className="grid gap-2">
                            <label className="text-sm font-medium text-gray-700">{t('users.dialog.username')} <span className="text-red-400">{t('users.dialog.usernameRequired')}</span></label>
                            <input
                                type="text"
                                value={createForm.username}
                                onChange={(e) => setCreateForm({ ...createForm, username: e.target.value })}
                                placeholder={t('users.dialog.usernamePlaceholder')}
                                className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-[#0071e3]/30 focus:border-[#0071e3]/40"
                            />
                        </div>
                        <div className="grid gap-2">
                            <label className="text-sm font-medium text-gray-700">{t('users.dialog.password')} <span className="text-red-400">{t('users.dialog.passwordRequired')}</span></label>
                            <div className="relative">
                                <input
                                    type={showCreatePassword ? 'text' : 'password'}
                                    value={createForm.password}
                                    onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })}
                                    placeholder={t('users.dialog.passwordPlaceholder')}
                                    className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 pr-10 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-[#0071e3]/30 focus:border-[#0071e3]/40"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowCreatePassword(!showCreatePassword)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                                >
                                    {showCreatePassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                        </div>
                        <div className="grid gap-2">
                            <label className="text-sm font-medium text-gray-700">{t('users.dialog.displayName')}</label>
                            <input
                                type="text"
                                value={createForm.display_name}
                                onChange={(e) => setCreateForm({ ...createForm, display_name: e.target.value })}
                                placeholder={t('users.dialog.displayNamePlaceholder')}
                                className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-[#0071e3]/30 focus:border-[#0071e3]/40"
                            />
                        </div>
                        <div className="grid gap-2">
                            <label className="text-sm font-medium text-gray-700">{t('users.dialog.role')}</label>
                            <div className="flex gap-3">
                                <button
                                    type="button"
                                    onClick={() => setCreateForm({ ...createForm, role: 'user' })}
                                    className={cn(
                                        'flex-1 flex items-center justify-center gap-2 rounded-xl border-2 px-4 py-3 text-sm font-medium transition-all',
                                        createForm.role === 'user'
                                            ? 'border-blue-500 bg-blue-50 text-blue-700'
                                            : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
                                    )}
                                >
                                    <UserIcon className="h-4 w-4" />
                                    {t('users.role.user')}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setCreateForm({ ...createForm, role: 'admin' })}
                                    className={cn(
                                        'flex-1 flex items-center justify-center gap-2 rounded-xl border-2 px-4 py-3 text-sm font-medium transition-all',
                                        createForm.role === 'admin'
                                            ? 'border-amber-500 bg-amber-50 text-amber-700'
                                            : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
                                    )}
                                >
                                    <Shield className="h-4 w-4" />
                                    {t('users.role.admin')}
                                </button>
                            </div>
                        </div>
                        <Button
                            onClick={handleCreate}
                            disabled={creating || !createForm.username.trim() || !createForm.password}
                            className="w-full rounded-xl bg-[#0071e3] hover:bg-[#0077ED] text-white shadow-sm h-10"
                        >
                            {creating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                            {creating ? t('users.dialog.creating') : t('users.dialog.createButton')}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>

            {/* 编辑用户对话框 */}
            <Dialog open={isEditOpen} onOpenChange={setIsEditOpen}>
                <DialogContent className="sm:max-w-[440px] bg-white/95 border-white/20 shadow-2xl">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Pencil className="h-5 w-5 text-[#0071e3]" />
                            {t('users.dialog.editTitle', { username: editingUser?.username })}
                        </DialogTitle>
                    </DialogHeader>
                    <div className="grid gap-5 py-4">
                        <div className="grid gap-2">
                            <label className="text-sm font-medium text-gray-700">{t('users.dialog.displayName')}</label>
                            <input
                                type="text"
                                value={editForm.display_name ?? ''}
                                onChange={(e) => setEditForm({ ...editForm, display_name: e.target.value })}
                                className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-[#0071e3]/30 focus:border-[#0071e3]/40"
                            />
                        </div>
                        <div className="grid gap-2">
                            <label className="text-sm font-medium text-gray-700">
                                {t('users.dialog.resetPassword')} <span className="text-xs text-gray-400 font-normal">{t('users.dialog.resetPasswordHint')}</span>
                            </label>
                            <div className="relative">
                                <input
                                    type={showEditPassword ? 'text' : 'password'}
                                    value={editPassword}
                                    onChange={(e) => setEditPassword(e.target.value)}
                                    placeholder={t('users.dialog.newPasswordPlaceholder')}
                                    className="flex h-10 w-full rounded-xl border border-gray-200 bg-white/50 px-4 py-2 pr-10 text-sm shadow-sm transition-all focus:outline-none focus:ring-2 focus:ring-[#0071e3]/30 focus:border-[#0071e3]/40"
                                />
                                <button
                                    type="button"
                                    onClick={() => setShowEditPassword(!showEditPassword)}
                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                                >
                                    {showEditPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                                </button>
                            </div>
                        </div>
                        {editingUser?.username !== currentUser?.username && (
                            <>
                                <div className="grid gap-2">
                                    <label className="text-sm font-medium text-gray-700">{t('users.dialog.role')}</label>
                                    <div className="flex gap-3">
                                        <button
                                            type="button"
                                            onClick={() => setEditForm({ ...editForm, role: 'user' })}
                                            className={cn(
                                                'flex-1 flex items-center justify-center gap-2 rounded-xl border-2 px-4 py-3 text-sm font-medium transition-all',
                                                editForm.role === 'user'
                                                    ? 'border-blue-500 bg-blue-50 text-blue-700'
                                                    : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
                                            )}
                                        >
                                            <UserIcon className="h-4 w-4" />
                                            {t('users.role.user')}
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => setEditForm({ ...editForm, role: 'admin' })}
                                            className={cn(
                                                'flex-1 flex items-center justify-center gap-2 rounded-xl border-2 px-4 py-3 text-sm font-medium transition-all',
                                                editForm.role === 'admin'
                                                    ? 'border-amber-500 bg-amber-50 text-amber-700'
                                                    : 'border-gray-200 bg-white text-gray-500 hover:border-gray-300'
                                            )}
                                        >
                                            <Shield className="h-4 w-4" />
                                            {t('users.role.admin')}
                                        </button>
                                    </div>
                                </div>
                                <div className="flex items-center justify-between p-3.5 rounded-xl border border-gray-200 bg-white/50">
                                    <div>
                                        <p className="text-sm font-medium text-gray-700">{t('users.dialog.enabledStatus')}</p>
                                        <p className="text-xs text-gray-400 mt-0.5">{t('users.dialog.enabledHint')}</p>
                                    </div>
                                    <button
                                        type="button"
                                        onClick={() => setEditForm({ ...editForm, enabled: !editForm.enabled })}
                                        className={cn(
                                            'relative inline-flex h-6 w-10 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors',
                                            editForm.enabled ? 'bg-green-500' : 'bg-gray-300'
                                        )}
                                    >
                                        <span
                                            className={cn(
                                                'pointer-events-none block h-5 w-5 rounded-full bg-white shadow-sm transition-transform',
                                                editForm.enabled ? 'translate-x-4' : 'translate-x-0'
                                            )}
                                        />
                                    </button>
                                </div>
                            </>
                        )}
                        <Button
                            onClick={handleUpdate}
                            disabled={updating}
                            className="w-full rounded-xl bg-[#0071e3] hover:bg-[#0077ED] text-white shadow-sm h-10"
                        >
                            {updating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Check className="mr-2 h-4 w-4" />}
                            {updating ? t('users.dialog.saving') : t('users.dialog.saveButton')}
                        </Button>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    )
}
