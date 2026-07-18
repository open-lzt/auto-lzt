/** Build-time feature flags.
 *
 * The canvas builder is DEFERRED, not deleted (D-5). Text is the authoring surface now — the bot
 * and the module registry — and the builder is behind this flag until it is worth the maintenance
 * it costs. The code stays in the tree deliberately: see README's "Deferred, not dead" note before
 * concluding that AuthoringMode and the deploy path are dead code, because a /cleanup pass that
 * reaches that conclusion will delete a working feature (R-16).
 *
 * Default OFF. Set `VITE_BUILDER_ENABLED=1` to build with authoring on.
 *
 * Flagging the UI off is a product decision, NOT a security boundary. The mutating endpoints are
 * still there and still guarded by the API key; hiding the buttons hides the buttons. The reason
 * this is honest rather than theatre is that the preview build ships no key to hide behind it:
 * auth_routes exposes only GET /auth/required, and the key is typed by an operator at runtime into
 * sessionStorage (AuthGate). A browser that never got a key cannot mutate whether or not the
 * button is drawn.
 */
export const BUILDER_ENABLED = import.meta.env.VITE_BUILDER_ENABLED === "1";
