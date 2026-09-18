import { RefreshCw } from 'lucide-react';

type ErrorRetryProps = { message: string; onRetry?: () => void };

export default function ErrorRetry({ message, onRetry }: ErrorRetryProps) {
  return (
    <div role="alert" aria-live="assertive" className="flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 px-3.5 py-2.5 text-[12.5px] text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
      <span>{message}</span>
      {onRetry && <button type="button" className="btn-quiet shrink-0 !px-2 !py-1 text-[11px]" onClick={onRetry}><RefreshCw size={13} aria-hidden="true" /> 重试</button>}
    </div>
  );
}
