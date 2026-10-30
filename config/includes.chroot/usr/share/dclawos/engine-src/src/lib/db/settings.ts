/*
 * DclawOS pricing settings adapter (GPL-2.0-or-later;
 * DclawOS-written). Phase 4 module 3.
 *
 * tierResolver.ts dynamically imports getPricingForModel from
 * "@/lib/db/settings" (upstream: pricing rows in the gateway DB).
 * DclawOS pricing lives in the vendored pricing constants
 * (@/shared/constants/pricing), the same source providerCostData
 * uses, so this adapter is a direct delegation — same function,
 * local pricing table instead of DB rows.
 */

export { getPricingForModel } from "@/shared/constants/pricing";
