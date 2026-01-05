/**
 * PDF 导出工具函数
 * 
 * 使用 html2canvas 将 DOM 元素渲染为图片，再用 jsPDF 生成 PDF
 */
import html2canvas from 'html2canvas'
import jsPDF from 'jspdf'

/**
 * 将不支持的现代 CSS 颜色替换为标准颜色
 * html2canvas 不支持 oklch, oklab, lch, lab, color() 等现代 CSS 颜色函数
 */
function replaceUnsupportedColors(doc: Document) {
    // 不支持的颜色函数关键字
    const unsupportedPatterns = ['oklch', 'oklab', 'lch(', 'lab(', 'color(', 'color-mix']

    const allElements = doc.querySelectorAll('*')
    allElements.forEach((el) => {
        const computed = window.getComputedStyle(el as Element)
        const htmlEl = el as HTMLElement

        // 检查并替换不支持的颜色
        const colorProps = ['color', 'backgroundColor', 'borderColor', 'borderTopColor', 'borderRightColor', 'borderBottomColor', 'borderLeftColor', 'outlineColor']
        colorProps.forEach(prop => {
            const cssProp = prop.replace(/([A-Z])/g, '-$1').toLowerCase()
            const value = computed.getPropertyValue(cssProp)

            // 检查是否包含任何不支持的颜色函数
            const isUnsupported = unsupportedPatterns.some(pattern => value && value.includes(pattern))

            if (isUnsupported) {
                // 替换为安全的颜色
                if (prop === 'backgroundColor') {
                    htmlEl.style.backgroundColor = '#f9fafb' // gray-50
                } else if (prop === 'color') {
                    htmlEl.style.color = '#1f2937' // gray-800
                } else {
                    htmlEl.style.setProperty(cssProp, '#e5e7eb') // gray-200
                }
            }
        })
    })
}


/**
 * 将 DOM 元素导出为 PDF
 * @param element 要导出的 DOM 元素
 * @param filename 文件名（不含扩展名）
 */
export async function exportToPdf(element: HTMLElement, filename: string): Promise<void> {
    // 1. 将 DOM 元素渲染为 Canvas（高分辨率）
    const canvas = await html2canvas(element, {
        scale: 2, // 2倍分辨率，确保清晰度
        useCORS: true, // 允许跨域图片
        logging: false,
        backgroundColor: '#ffffff',
        // 在克隆的 DOM 上替换不支持的颜色
        onclone: (doc) => {
            replaceUnsupportedColors(doc)
        },
        // 忽略不需要导出的元素
        ignoreElements: (el) => el.classList.contains('no-export')
    })

    // 2. 计算 PDF 尺寸 (A4: 210mm x 297mm)
    const imgWidth = 210
    const pageHeight = 297
    const imgHeight = (canvas.height * imgWidth) / canvas.width

    // 3. 创建 PDF 文档
    const pdf = new jsPDF('p', 'mm', 'a4')
    const imgData = canvas.toDataURL('image/png')

    // 4. 添加内容（支持多页）
    let heightLeft = imgHeight
    let position = 0

    // 添加第一页
    pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
    heightLeft -= pageHeight

    // 如果内容超过一页，添加更多页
    while (heightLeft > 0) {
        position = heightLeft - imgHeight
        pdf.addPage()
        pdf.addImage(imgData, 'PNG', 0, position, imgWidth, imgHeight)
        heightLeft -= pageHeight
    }

    // 5. 下载文件
    // 清理文件名中的非法字符
    const safeFilename = filename.replace(/[<>:"/\\|?*]/g, '_')
    pdf.save(`${safeFilename}.pdf`)
}
