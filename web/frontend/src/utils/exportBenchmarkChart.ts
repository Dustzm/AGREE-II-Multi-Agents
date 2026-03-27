import { ItemConsistency } from '@/services/api'

const CANVAS_WIDTH = 1400
const CANVAS_HEIGHT = 760
const Y_AXIS_STEPS = [0, 20, 40, 60, 80, 100]

interface ExportItemConsistencyChartOptions {
    xlsxFile: string
    items: ItemConsistency[]
    yAxisLabel: string
    itemLabelPrefix: string
}

function sanitizeFilename(filename: string): string {
    return filename.replace(/[<>:"/\\|?*]+/g, '_').replace(/\s+/g, '_')
}

function getItemNumber(itemId: string): number {
    const match = itemId.match(/(\d+)$/)
    return match ? Number(match[1]) : 0
}

function getBarPalette(value: number) {
    if (value < 60) {
        return {
            start: '#FDA4AF',
            end: '#FB7185',
            shadow: 'rgba(244, 63, 94, 0.22)'
        }
    }
    if (value < 80) {
        return {
            start: '#FCD34D',
            end: '#F59E0B',
            shadow: 'rgba(245, 158, 11, 0.22)'
        }
    }
    return {
        start: '#9AEC8D',
        end: '#7EDC82',
        shadow: 'rgba(16, 185, 129, 0.20)'
    }
}

function topRoundedRect(
    ctx: CanvasRenderingContext2D,
    x: number,
    y: number,
    width: number,
    height: number,
    radius: number
) {
    const boundedRadius = Math.min(radius, width / 2, height / 2)
    ctx.beginPath()
    ctx.moveTo(x + boundedRadius, y)
    ctx.lineTo(x + width - boundedRadius, y)
    ctx.quadraticCurveTo(x + width, y, x + width, y + boundedRadius)
    ctx.lineTo(x + width, y + height)
    ctx.lineTo(x, y + height)
    ctx.lineTo(x, y + boundedRadius)
    ctx.quadraticCurveTo(x, y, x + boundedRadius, y)
    ctx.closePath()
}

export async function exportItemConsistencyChart({
    xlsxFile,
    items,
    yAxisLabel,
    itemLabelPrefix
}: ExportItemConsistencyChartOptions): Promise<void> {
    if (!items.length) {
        throw new Error('没有可导出的条目一致性数据')
    }

    if ('fonts' in document) {
        await document.fonts.ready
    }

    const sortedItems = [...items].sort((a, b) => getItemNumber(a.item_id) - getItemNumber(b.item_id))
    const datasetTitle = xlsxFile.replace(/\.xlsx$/i, '')

    const canvas = document.createElement('canvas')
    canvas.width = CANVAS_WIDTH * 2
    canvas.height = CANVAS_HEIGHT * 2
    canvas.style.width = `${CANVAS_WIDTH}px`
    canvas.style.height = `${CANVAS_HEIGHT}px`

    const ctx = canvas.getContext('2d')
    if (!ctx) {
        throw new Error('无法初始化导出画布')
    }

    ctx.scale(2, 2)
    ctx.imageSmoothingEnabled = true
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, CANVAS_WIDTH, CANVAS_HEIGHT)

    const margin = {
        top: 84,
        right: 36,
        bottom: 156,
        left: 118
    }
    const chartLeft = margin.left
    const chartTop = margin.top
    const chartWidth = CANVAS_WIDTH - margin.left - margin.right
    const chartHeight = CANVAS_HEIGHT - margin.top - margin.bottom
    const chartBottom = chartTop + chartHeight

    ctx.fillStyle = '#111827'
    ctx.font = '700 26px "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(datasetTitle, CANVAS_WIDTH / 2, 42)

    ctx.save()
    ctx.translate(44, chartTop + chartHeight / 2)
    ctx.rotate(-Math.PI / 2)
    ctx.fillStyle = '#334155'
    ctx.font = '600 20px "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(yAxisLabel, 0, 0)
    ctx.restore()

    ctx.strokeStyle = '#cbd5e1'
    ctx.lineWidth = 1
    ctx.setLineDash([4, 4])

    Y_AXIS_STEPS.forEach((step) => {
        const y = chartBottom - (step / 100) * chartHeight

        ctx.beginPath()
        ctx.moveTo(chartLeft, y)
        ctx.lineTo(chartLeft + chartWidth, y)
        ctx.stroke()

        ctx.fillStyle = '#64748b'
        ctx.font = '600 16px "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif'
        ctx.textAlign = 'right'
        ctx.textBaseline = 'middle'
        ctx.fillText(`${step}`, chartLeft - 16, y)
    })

    ctx.setLineDash([])
    ctx.strokeStyle = '#94a3b8'
    ctx.lineWidth = 2

    ctx.beginPath()
    ctx.moveTo(chartLeft, chartBottom)
    ctx.lineTo(chartLeft + chartWidth, chartBottom)
    ctx.stroke()

    const slotWidth = chartWidth / sortedItems.length
    const barWidth = Math.min(42, Math.max(30, slotWidth * 0.72))

    sortedItems.forEach((item, index) => {
        const value = Math.max(0, Math.min(100, item.consistency * 100))
        const barHeight = (value / 100) * chartHeight
        const centerX = chartLeft + slotWidth * index + slotWidth / 2
        const barX = centerX - barWidth / 2
        const barY = chartBottom - barHeight
        const palette = getBarPalette(value)

        const gradient = ctx.createLinearGradient(0, barY, 0, chartBottom)
        gradient.addColorStop(0, palette.start)
        gradient.addColorStop(1, palette.end)
        ctx.fillStyle = gradient
        ctx.shadowColor = palette.shadow
        ctx.shadowBlur = 16
        ctx.shadowOffsetX = 0
        ctx.shadowOffsetY = 6
        topRoundedRect(ctx, barX, barY, barWidth, barHeight, 10)
        ctx.fill()
        ctx.shadowColor = 'transparent'
        ctx.shadowBlur = 0
        ctx.shadowOffsetX = 0
        ctx.shadowOffsetY = 0

        ctx.fillStyle = '#1f2937'
        ctx.font = '600 14px "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'bottom'
        ctx.fillText(`${value.toFixed(1)}%`, centerX, barY - 6)

        const itemIndex = getItemNumber(item.item_id)
        ctx.fillStyle = '#111827'
        ctx.font = '500 13px "Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif'
        ctx.textAlign = 'center'
        ctx.textBaseline = 'top'
        ctx.fillText(`${itemLabelPrefix} ${itemIndex}`, centerX, chartBottom + 36)
    })

    const link = document.createElement('a')
    link.href = canvas.toDataURL('image/png')
    link.download = `${sanitizeFilename(datasetTitle)}_item_consistency_chart.png`
    link.click()
}
