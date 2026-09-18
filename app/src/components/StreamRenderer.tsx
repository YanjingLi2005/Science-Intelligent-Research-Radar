import { memo } from 'react';
import { Square } from 'lucide-react';

interface StreamRendererProps {
  text: string;
  isStreaming: boolean;
  onAbort?: () => void;
  className?: string;
  placeholder?: string;
}

export const StreamRenderer = memo(function StreamRenderer({
  text,
  isStreaming,
  onAbort,
  className = '',
  placeholder = '正在组织语言…',
}: StreamRendererProps) {
  if (!text && isStreaming) {
    return (
      <div className={`flex items-center justify-between gap-3 text-xs text-neutral-400 dark:text-white/70 ${className}`}>
        <div className="flex items-center gap-2">
          <span className="inline-block h-2 w-2 animate-ping rounded-full bg-teal" />
          <span>{placeholder}</span>
        </div>
        {onAbort && (
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded border border-hairline px-2 py-1 text-[11px] text-neutral-500 hover:bg-neutral-100 dark:border-white/10 dark:text-white/70 dark:hover:bg-white/[0.06]"
            onClick={onAbort}
          >
            <Square size={11} className="fill-current text-red-500" />
            <span>停止生成</span>
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`relative ${className}`}>
      <div className="whitespace-pre-wrap break-words text-sm leading-7 text-neutral-800 dark:text-white/95">
        {text}
        {isStreaming && (
          <span
            className="ml-0.5 inline-block h-4 w-1.5 translate-y-0.5 bg-teal animate-pulse"
            aria-hidden="true"
          />
        )}
      </div>
      {isStreaming && onAbort && (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            className="inline-flex items-center gap-1.5 rounded-md border border-hairline bg-white/80 px-2.5 py-1 text-[11px] font-medium text-neutral-600 shadow-sm backdrop-blur transition hover:bg-neutral-100 dark:border-white/10 dark:bg-white/[0.06] dark:text-white/80 dark:hover:bg-white/10"
            onClick={onAbort}
          >
            <Square size={11} className="fill-current text-red-500" />
            <span>停止生成</span>
          </button>
        </div>
      )}
    </div>
  );
});
