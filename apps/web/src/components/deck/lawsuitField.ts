import { facts } from "@/lib/facts";
import type { FieldOrigin } from "./DotField";

export const SARAS_SHOP_DOTS = 1;
export const lawsuitFieldCount = facts.adaLawsuitsFiled2025.value + SARAS_SHOP_DOTS;
export const sarasShopInField: FieldOrigin = { x: 0.5, y: 0.86 };
