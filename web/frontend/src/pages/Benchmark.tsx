import { MessageView } from '@/components/MessageView'
import { asMessage, LocalizedError, responseMessage, type UiMessage } from '@/i18n/messages'
import { notifyError, notifySuccess } from '@/i18n/notify'
import { useEffect, useCallback, useReducer } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertCircle, Loader2 } from 'lucide-react'
import { api, HumanFileInfo, BatchInfo, BenchmarkResultInfo, BenchmarkSummary } from '@/services/api'
import { Sidebar } from '@/components/benchmark/Sidebar'
import { Workspace } from '@/components/benchmark/Workspace'

// ==================== 状态管理 ====================

interface BenchmarkState {
  // 数据状态
  humanFiles: HumanFileInfo[]
  batches: BatchInfo[]
  results: BenchmarkResultInfo[]

  // UI 状态
  loading: boolean
  error: UiMessage | null
  isRunning: boolean
  loadingDetail: boolean

  // 当前选中的内容
  selectedResult: string | null
  viewMode: 'empty' | 'new' | 'detail'
  currentSummary: BenchmarkSummary | null
  currentXlsxFile: string
  currentBatchIds: string[]
  currentHasChanges: boolean
  currentIsSaving: boolean
}

type BenchmarkAction =
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: UiMessage | null }
  | { type: 'SET_HUMAN_FILES'; payload: HumanFileInfo[] }
  | { type: 'SET_BATCHES'; payload: BatchInfo[] }
  | { type: 'SET_RESULTS'; payload: BenchmarkResultInfo[] }
  | { type: 'SET_RUNNING'; payload: boolean }
  | { type: 'SET_LOADING_DETAIL'; payload: boolean }
  | { type: 'SELECT_RESULT'; payload: string | null }
  | { type: 'SET_VIEW_NEW' }
  | { type: 'SET_VIEW_DETAIL'; payload: { summary: BenchmarkSummary; xlsxFile: string; batchIds: string[] } }
  | { type: 'SET_VIEW_EMPTY' }
  | { type: 'SET_HAS_CHANGES'; payload: boolean }
  | { type: 'SET_SAVING'; payload: boolean }

const initialState: BenchmarkState = {
  humanFiles: [],
  batches: [],
  results: [],
  loading: true,
  error: null,
  isRunning: false,
  loadingDetail: false,
  selectedResult: null,
  viewMode: 'empty',
  currentSummary: null,
  currentXlsxFile: '',
  currentBatchIds: [],
  currentHasChanges: false,
  currentIsSaving: false
}

function isSelectableBatch(name: string): boolean {
  return name.startsWith('batch_') && name !== 'batch_state'
}

function benchmarkReducer(state: BenchmarkState, action: BenchmarkAction): BenchmarkState {
  switch (action.type) {
    case 'SET_LOADING':
      return { ...state, loading: action.payload }
    case 'SET_ERROR':
      return { ...state, error: action.payload }
    case 'SET_HUMAN_FILES':
      return { ...state, humanFiles: action.payload }
    case 'SET_BATCHES':
      return { ...state, batches: action.payload }
    case 'SET_RESULTS':
      return { ...state, results: action.payload }
    case 'SET_RUNNING':
      return { ...state, isRunning: action.payload }
    case 'SET_LOADING_DETAIL':
      return { ...state, loadingDetail: action.payload }
    case 'SELECT_RESULT':
      return { ...state, selectedResult: action.payload }
    case 'SET_VIEW_NEW':
      return { ...state, viewMode: 'new', selectedResult: null }
    case 'SET_VIEW_DETAIL':
      return {
        ...state,
        viewMode: 'detail',
        currentSummary: action.payload.summary,
        currentXlsxFile: action.payload.xlsxFile,
        currentBatchIds: action.payload.batchIds
      }
    case 'SET_VIEW_EMPTY':
      return { ...state, viewMode: 'empty', selectedResult: null, currentSummary: null }
    case 'SET_HAS_CHANGES':
      return { ...state, currentHasChanges: action.payload }
    case 'SET_SAVING':
      return { ...state, currentIsSaving: action.payload }
    default:
      return state
  }
}

export function Benchmark() {
  const { t } = useTranslation()
  const [state, dispatch] = useReducer(benchmarkReducer, initialState)

  // ==================== 数据加载 ====================

  const loadData = useCallback(async () => {
    dispatch({ type: 'SET_LOADING', payload: true })
    dispatch({ type: 'SET_ERROR', payload: null })

    try {
      const [humanFilesRes, batchesRes, resultsRes] = await Promise.allSettled([
        api.listHumanFiles(),
        api.listBatches(),
        api.listBenchmarkResults()
      ])

      const failed = [humanFilesRes, batchesRes, resultsRes].find(result => result.status === 'rejected')
      if (failed?.status === 'rejected') dispatch({ type: 'SET_ERROR', payload: asMessage(failed.reason) })

      if (humanFilesRes.status === 'fulfilled') {
        dispatch({ type: 'SET_HUMAN_FILES', payload: humanFilesRes.value.files })
      }
      if (batchesRes.status === 'fulfilled') {
        dispatch({
          type: 'SET_BATCHES',
          payload: batchesRes.value.batches.filter((batch) => isSelectableBatch(batch.name))
        })
      }
      if (resultsRes.status === 'fulfilled') {
        dispatch({ type: 'SET_RESULTS', payload: resultsRes.value.results })
      }
    } catch (error) {
      console.error('加载数据失败:', error)
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false })
    }
  }, [])

  useEffect(() => {
    loadData()
  }, [loadData])

  // ==================== 事件处理 ====================

  // 选择历史结果
  const handleSelectResult = useCallback(async (filename: string) => {
    // 如果已经在加载这个文件，忽略重复点击
    if (state.loadingDetail) return

    // 如果点击的是当前已选中的文件，不重复加载
    if (state.selectedResult === filename && state.viewMode === 'detail') return

    // 先设置选中文件，但保持 viewMode 为 'detail'（如果已有数据）
    // 这样可以保持现有内容显示，只叠加加载覆盖层
    dispatch({ type: 'SELECT_RESULT', payload: filename })
    dispatch({ type: 'SET_LOADING_DETAIL', payload: true })

    try {
      const detail = await api.getBenchmarkDetail(filename)
      // 数据加载完成后，一次性更新所有状态
      dispatch({
        type: 'SET_VIEW_DETAIL',
        payload: {
          summary: detail.summary,
          xlsxFile: detail.xlsx_file,
          batchIds: detail.batch_ids
        }
      })
    } catch (error) {
      console.error(error)
      dispatch({ type: 'SET_ERROR', payload: asMessage(error, 'benchmark.details.loadFailed') })
    } finally {
      // 无论成功还是失败，都清空 loadingDetail 状态
      dispatch({ type: 'SET_LOADING_DETAIL', payload: false })
    }
  }, [state.selectedResult, state.viewMode, state.loadingDetail])

  // 刷新当前实验详情
  const handleRefreshDetail = useCallback(async () => {
    if (!state.currentXlsxFile) return

    dispatch({ type: 'SET_LOADING_DETAIL', payload: true })

    try {
      const detail = await api.getBenchmarkDetail(state.currentXlsxFile)
      // 数据加载完成后，一次性更新所有状态
      dispatch({
        type: 'SET_VIEW_DETAIL',
        payload: {
          summary: detail.summary,
          xlsxFile: detail.xlsx_file,
          batchIds: detail.batch_ids
        }
      })
    } catch (error) {
      console.error(error)
      dispatch({ type: 'SET_ERROR', payload: asMessage(error, 'benchmark.header.refreshError') })
    } finally {
      dispatch({ type: 'SET_LOADING_DETAIL', payload: false })
    }
  }, [state.currentXlsxFile])

  // 删除历史结果
  const handleDeleteResult = useCallback(async (filename: string) => {
    try {
      await api.deleteBenchmarkResult(filename)
      notifySuccess('benchmark.header.deleted')

      // 如果删除的是当前选中的，清空视图
      if (state.selectedResult === filename) {
        dispatch({ type: 'SET_VIEW_EMPTY' })
      }

      await loadData()
    } catch (error) {
      console.error(error)
      notifyError(error, 'benchmark.header.deleteFailed')
    }
  }, [state.selectedResult, loadData])

  // 点击新建实验
  const handleClickNew = useCallback(() => {
    dispatch({ type: 'SET_VIEW_NEW' })
  }, [])

  // 执行实验
  const handleRun = useCallback(async (humanFile: string, batchIds: string[], threshold: number) => {
    dispatch({ type: 'SET_RUNNING', payload: true })

    try {
      const response = await api.runBenchmark(humanFile, batchIds, threshold)

      if (response.status === 'success' && response.summary && response.xlsx_file) {
        // 刷新结果列表
        await loadData()

        // 自动切换到详情视图
        dispatch({
          type: 'SET_VIEW_DETAIL',
          payload: {
            summary: response.summary,
            xlsxFile: response.xlsx_file,
            batchIds
          }
        })

        notifySuccess('benchmark.run.success')
      } else {
        throw new LocalizedError(responseMessage({ ...response }))
      }
    } catch (error) {
      console.error(error)
      notifyError(error, 'benchmark.form.error.run')
    } finally {
      dispatch({ type: 'SET_RUNNING', payload: false })
    }
  }, [loadData])

  // 保存修改
  const handleSave = useCallback(() => {
    // 这个会在 GuideDetailTab 中处理
    dispatch({ type: 'SET_HAS_CHANGES', payload: false })
  }, [])

  // 下载文件
  const handleDownload = useCallback(() => {
    if (state.currentXlsxFile) {
      window.open(api.getBenchmarkDownloadUrl(state.currentXlsxFile), '_blank')
    }
  }, [state.currentXlsxFile])

  // 删除当前实验
  const handleDelete = useCallback(async () => {
    if (state.currentXlsxFile) {
      await handleDeleteResult(state.currentXlsxFile)
    }
  }, [state.currentXlsxFile, handleDeleteResult])



  // ==================== 渲染 ====================

  if (state.loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-slate-50">
        <Loader2 className="w-8 h-8 animate-spin text-blue-500" />
      </div>
    )
  }

  return (
    <div className="h-full flex-1 flex overflow-hidden bg-slate-100">
      {/* 左侧边栏 */}
      <Sidebar
        results={state.results}
        selectedResult={state.selectedResult}
        isLoading={state.loadingDetail}
        onSelectResult={handleSelectResult}
        onDeleteResult={handleDeleteResult}
        onClickNew={handleClickNew}
        onViewMode={state.viewMode === 'new' ? 'new' : 'history'}
      />

      {/* 右侧主工作区 */}
      <Workspace
        mode={state.viewMode}
        summary={state.currentSummary}
        xlsxFile={state.currentXlsxFile}
        batchIds={state.currentBatchIds}
        humanFiles={state.humanFiles}
        batches={state.batches}
        isRunning={state.isRunning}
        isLoadingDetail={state.loadingDetail}
        hasChanges={state.currentHasChanges}
        isSaving={state.currentIsSaving}
        onRun={handleRun}
        onSave={handleSave}
        onDownload={handleDownload}
        onDelete={handleDelete}
        onRefresh={handleRefreshDetail}
      />

      {/* 错误提示 */}
      {state.error && (
        <div className="fixed top-4 right-4 z-50 max-w-md">
          <div className="bg-red-50 border border-red-200 rounded-xl p-4 flex items-center gap-3 shadow-lg">
            <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
            <div className="text-red-700 flex-1"><MessageView message={state.error} /></div>
            <button
              aria-label={t('ui.close')}
              onClick={() => dispatch({ type: 'SET_ERROR', payload: null })}
              className="text-red-500 hover:text-red-700"
            >
              ×
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
