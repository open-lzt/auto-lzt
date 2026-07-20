# Panel: three traps found in a browser

Each looked like working code. No test caught any of them.

---

## `Main` from `@open-lzt/ui` is a sidebar grid, not a content wrapper

**What happens:** a single child inside `Main` lands in the sidebar-width column, and the content
column stays empty.

```css
.lzt-main { display: grid; grid-template-columns: var(--lzt-sidebar) 1fr; }
```

**Why it hides:** the name. `Main` reads as a semantic content wrapper rather than a two-slot
layout. A grid with one child does not break and does not complain — it quietly drops that child
into the first track. With two children the component behaves exactly as expected, so the bug is
only visible on a screen that has no sidebar.

**How to catch it:** read the computed width, not the markup. The panel's `.panel-view` measured
`w=240` while its own `max-width` was `1240px` — that gap between declared and actual is the grid's
signature.

**What to do:** use `Main` only when there really are two children. A screen without a sidebar wants
a plain `<main>` carrying its own class.

---

## SSE: headers alone do not make a connection open

**What happens:** a buffering proxy holds the response until the first byte of the **body**. On a
quiet channel that byte is the first heartbeat, so "connected" silently becomes "connected within
`heartbeat_s` seconds".

**Why it hides:** from the server side everything looks healthy. Status `200` and `content-type:
text/event-stream` go out immediately and the access log shows the request accepted. Only the client
is waiting, and that is easy to blame on the network.

**The trap inside the trap:** this is **not** an nginx-only problem. The vite dev proxy buffers the
same way, so the effect is there on localhost too — where there is nowhere to put
`proxy_buffering off`.

**Measured:** the indicator sat in `connecting` for 13 seconds with `heartbeat_s = 15`. Once the
stream emitted a frame before its first wait, it opened immediately.

**Rule:** every SSE stream writes a frame **before** its first `await`. A comment (`: open`) is
enough — the spec makes it inert, so `EventSource` fires `open` and never surfaces it as a message.

---

## The market mock in `dev.py` answers every path identically

**What happens:** a node whose response decodes into a model with required fields fails validation.
In the interface this reads as a failed task, not as a mock problem.

```
RunFailed: run ... failed at step lots: 31 validation errors for ListUserResponse
items      Field required [type=missing, input_value={'status': 'ok', 'message': 'done'}]
totalItems Field required [type=missing, input_value={'status': 'ok', 'message': 'done'}]
```

**Why it hides:** every other node is satisfied by the flat `{"status": "ok"}`, so the mock looks
fine indefinitely. The one node that reads a substantive response is the one that breaks — and it
breaks on the first flow run, not at startup.

**Route order is part of the rule:** `respx` takes the **first** match, so a specific path must be
registered **before** the host-wide catch-all. Reversed, the general route swallows the specific one
without saying so.

**What to do when adding a node with a rich response:** register a path-specific route in
`_maybe_mock_market`, and build the body with `minimal_instance` from
`tests/fixtures/mock_lzt_server.py` — it constructs a valid instance from the model's own fields.
These models are not worth typing by hand: `ListUserResponse` has around thirty required fields and
`ListUserItem` more than forty.

**Why this matters more than it looks:** `dev.py` is how the project is run first. A flow that
cannot complete there looks like a broken product.
