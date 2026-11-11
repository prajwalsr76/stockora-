import { createContext, useContext } from "react";
import type { StockRow } from "../api";

export type Page = "welcome" | "setup" | "overview" | "stock" | "expiry" | "counter" | "invoices" | "restock" | "bills" | "udhaar" | "signals" | "ask";

export type AppCtx = {
  go: (p: Page) => void;
  openProduct: (id: number) => void;
  stock: StockRow[];
  refreshStock: () => void;
  stockLoaded: boolean;
};

export const AppContext = createContext<AppCtx>({ go: () => {}, openProduct: () => {}, stock: [], refreshStock: () => {}, stockLoaded: false });
export const useApp = () => useContext(AppContext);
