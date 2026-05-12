(function () {
  function mergeById(existing, incoming) {
    const map = new Map();
    for (const item of existing || []) {
      if (item && item.id) {
        map.set(item.id, item);
      }
    }
    for (const item of incoming || []) {
      if (item && item.id) {
        map.set(item.id, { ...(map.get(item.id) || {}), ...item });
      }
    }
    return Array.from(map.values());
  }

  function mergeBySeq(existing, incoming) {
    const map = new Map();
    for (const item of existing || []) {
      if (item && Number.isFinite(Number(item.seq_no))) {
        map.set(Number(item.seq_no), item);
      }
    }
    for (const item of incoming || []) {
      if (item && Number.isFinite(Number(item.seq_no))) {
        const seq = Number(item.seq_no);
        map.set(seq, { ...(map.get(seq) || {}), ...item });
      }
    }
    return Array.from(map.values()).sort((a, b) => Number(a.seq_no) - Number(b.seq_no));
  }

  window.PSOPRuntimeEvents = {
    mergeById,
    mergeBySeq
  };
})();
