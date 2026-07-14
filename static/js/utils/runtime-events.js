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

  function shouldReplaceTaskStatus(current, incoming) {
    if (!incoming || !incoming.run_id) {
      return false;
    }
    if (!current || current.run_id !== incoming.run_id) {
      return true;
    }
    const currentSeq = Number(current.snapshot_seq) || 0;
    const incomingSeq = Number(incoming.snapshot_seq) || 0;
    if (incomingSeq !== currentSeq) {
      return incomingSeq > currentSeq;
    }
    return String(incoming.updated_at || "") > String(current.updated_at || "");
  }

  window.PSOPRuntimeEvents = {
    mergeById,
    mergeBySeq,
    shouldReplaceTaskStatus
  };
})();
