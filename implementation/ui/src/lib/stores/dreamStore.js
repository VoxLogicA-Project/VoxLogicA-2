import { writable } from "svelte/store";

const initial = {
  visible: false,
  dissolving: false,
  nodeIds: [],
};

const store = writable({ ...initial });

export const dreamState = {
  subscribe: store.subscribe,
};

export const showDream = (nodeIds = []) => {
  const nextIds = Array.isArray(nodeIds) ? nodeIds.filter((id) => Boolean(id)) : [];
  store.set({
    visible: true,
    dissolving: false,
    nodeIds: nextIds.length ? nextIds : ["node"],
  });
};

export const dissolveDream = () => {
  store.update((state) => ({
    ...state,
    dissolving: true,
  }));
};

export const clearDream = () => {
  store.set({ ...initial });
};
