import "@testing-library/jest-dom";

// jsdom has no ResizeObserver — @xyflow/react's <ReactFlow> observes its container on mount, so
// any test that renders the full canvas (not just a bare node component) needs this stub.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
