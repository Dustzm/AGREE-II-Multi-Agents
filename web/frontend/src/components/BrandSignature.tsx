import { cn } from '@/utils/cn'

interface BrandSignatureProps {
    compact?: boolean
    inverted?: boolean
    className?: string
}

export function BrandSignature({ compact = false, inverted = false, className }: BrandSignatureProps) {
    return (
        <div className={cn('flex items-center gap-4', className)}>
            <div className={cn(
                'flex shrink-0 items-center justify-center rounded-[24px] border shadow-[0_20px_50px_-28px_rgba(0,0,0,0.35)]',
                compact ? 'h-12 w-12 rounded-[18px]' : 'h-16 w-16',
                inverted
                    ? 'border-white/12 bg-white/96'
                    : 'border-black/6 bg-[#f5f5f7]'
            )}>
                <img
                    src="/ama/ama-logo.svg"
                    alt="AMA"
                    className={cn(compact ? 'h-9 w-9' : 'h-12 w-12')}
                />
            </div>

            <div className="min-w-0">
                <div className={cn(
                    'font-semibold tracking-[-0.04em]',
                    compact ? 'text-[1.125rem] leading-none' : 'text-[2rem] leading-[0.95]',
                    inverted ? 'text-white' : 'text-[#1d1d1f]'
                )}>
                    AMA
                </div>
                <div className={cn(
                    'mt-1 font-medium uppercase tracking-[0.24em]',
                    compact ? 'text-[0.56rem]' : 'text-[0.7rem]',
                    inverted ? 'text-white/52' : 'text-black/42'
                )}>
                    AGREE-II Multi-Agents
                </div>
            </div>
        </div>
    )
}
