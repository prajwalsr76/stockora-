import { Component, type ErrorInfo, type ReactNode } from "react";
import { t } from "../i18n";

type Props = { children: ReactNode; resetKey: string };
type State = { error: Error | null };

/** Keeps one broken page from blanking the whole app. Resets when the page changes. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Stockora page error", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" className="rounded-xl border border-out/30 bg-out-soft px-5 py-4">
        <p className="font-medium text-out">{t("This page hit a problem and stopped.")}</p>
        <p className="text-sm">{this.state.error.message}</p>
        <button className="mt-2 text-sm font-medium text-out underline" onClick={() => this.setState({ error: null })}>
          {t("Reload this page")}
        </button>
      </div>
    );
  }
}
