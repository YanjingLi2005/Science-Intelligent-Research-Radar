import { Component, type ReactNode } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
  mode?: 'fullscreen' | 'page' | 'inline';
  onReset?: () => void;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo): void {
    console.error('[ErrorBoundary]', error, errorInfo.componentStack);
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) {
        return this.props.fallback;
      }

      const mode = this.props.mode ?? 'page';

      if (mode === 'inline') {
        return (
          <div role="alert" className="my-2 flex items-center justify-between gap-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700 dark:border-red-500/25 dark:bg-red-500/10 dark:text-red-300">
            <div className="flex items-center gap-2">
              <AlertTriangle size={14} className="shrink-0" />
              <span>模块渲染异常：{this.state.error?.message || '未知错误'}</span>
            </div>
            <button
              type="button"
              className="btn-quiet shrink-0 !px-2 !py-1 text-[11px]"
              onClick={this.handleReset}
            >
              <RefreshCw size={12} /> 重试
            </button>
          </div>
        );
      }

      if (mode === 'fullscreen') {
        return (
          <div className="flex min-h-screen items-center justify-center bg-[#0d0d0f] text-white">
            <div className="max-w-md px-6 text-center">
              <h1 className="mb-3 text-xl font-semibold tracking-tight">应用遇到意外错误</h1>
              <p className="mb-6 text-sm text-neutral-400">
                {this.state.error?.message || '渲染组件时发生未捕获异常。'}
              </p>
              <div className="flex items-center justify-center gap-3">
                <button
                  type="button"
                  className="rounded-lg bg-white px-4 py-2 text-sm font-medium text-[#111113] hover:opacity-90"
                  onClick={() => window.location.reload()}
                >
                  刷新页面
                </button>
                <button
                  type="button"
                  className="rounded-lg border border-white/20 px-4 py-2 text-sm font-medium text-white hover:bg-white/10"
                  onClick={this.handleReset}
                >
                  尝试恢复
                </button>
              </div>
            </div>
          </div>
        );
      }

      // Page / Card-level fallback (preserves Sidebar & Top bar)
      return (
        <div className="card my-8 p-8 text-center" role="alert">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-red-100 dark:bg-red-500/10">
            <AlertTriangle className="h-6 w-6 text-red-600 dark:text-red-400" />
          </div>
          <h2 className="mt-4 text-base font-semibold text-ink dark:text-white">页面渲染遇到问题</h2>
          <p className="mx-auto mt-2 max-w-md text-xs leading-relaxed text-neutral-500 dark:text-white/70">
            {this.state.error?.message || '组件加载或渲染时发生异常，请尝试重试或切换至其他页面。'}
          </p>
          <div className="mt-6 flex items-center justify-center gap-3">
            <button
              type="button"
              className="btn-primary !px-4 !py-2 text-xs"
              onClick={this.handleReset}
            >
              <RefreshCw size={13} /> 重新加载此模块
            </button>
            <button
              type="button"
              className="btn-secondary !px-4 !py-2 text-xs"
              onClick={() => window.location.reload()}
            >
              刷新整个页面
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

