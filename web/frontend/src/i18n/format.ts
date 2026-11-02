/** Keep user-provided date text when it cannot be interpreted as a date. */
export function formatDate(value: string, language: string, options?: Intl.DateTimeFormatOptions): string {
    const date = new Date(value.replace(' ', 'T'))
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString(language, options)
}
