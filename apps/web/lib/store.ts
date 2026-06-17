import { create } from "zustand";

interface CatenaUiState {
  selectedTableId: number | null;
  selectedPaperIds: number[];
  setSelectedTableId: (tableId: number | null) => void;
  setSelectedPaperIds: (paperIds: number[]) => void;
}

export const useCatenaUiStore = create<CatenaUiState>((set) => ({
  selectedTableId: null,
  selectedPaperIds: [],
  setSelectedTableId: (selectedTableId) => set({ selectedTableId }),
  setSelectedPaperIds: (selectedPaperIds) => set({ selectedPaperIds }),
}));
