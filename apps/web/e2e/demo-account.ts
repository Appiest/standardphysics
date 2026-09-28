/**
 * The seeded demo owner the browser tests sign in as.
 *
 * The API creates this account on startup when SP_SEED_SAMPLE_SHOP is on, and
 * generates a random password unless SP_SEED_OWNER_PASSWORD names one, so the
 * tests pin it here and hand the same value to the API they start.
 */
export const DEMO_EMAIL = process.env.SP_SEED_OWNER_EMAIL || "demo@standardphysics.app";
export const DEMO_PASSWORD = process.env.SP_SEED_OWNER_PASSWORD || "playwright-demo-owner";
export const SAMPLE_SHOP_NAME = "Sample boba shop";
