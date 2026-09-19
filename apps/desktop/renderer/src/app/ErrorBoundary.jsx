import React from "react";

/** Keep one failing component from blanking the whole window. */
export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("[lora] renderer failed", error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) {
      return this.props.children;
    }
    return (
      <main className="startup" role="alert">
        <h1>Lora</h1>
        <p>界面出错，已停止渲染：{error.message || String(error)}</p>
        <button type="button" onClick={() => globalThis.location?.reload()}>
          重新加载
        </button>
      </main>
    );
  }
}
