// Canvas builder is DEFERRED, not dead code (see README) — do not delete AuthoringMode/deploy path.
// Default OFF; set VITE_BUILDER_ENABLED=1 to build with authoring on.
// NOT a security boundary: mutating endpoints stay guarded by the API key regardless of this flag;
// the preview build is safe only because it ships no key (auth_routes exposes GET /auth/required only).
export const BUILDER_ENABLED = import.meta.env.VITE_BUILDER_ENABLED === "1";
