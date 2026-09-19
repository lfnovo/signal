/* Deterministic, weighted greedy modularity. No topic or association is changed. */
(() => {
  function clusterTopics(topics, edges) {
    const ids = new Set(topics.map(t => t.id));
    const degree = new Map(topics.map(t => [t.id, 0]));
    const links = edges.filter(e => ids.has(e.source) && ids.has(e.target) && e.source !== e.target && Number.isFinite(e.strength) && e.strength > 0);
    let total = 0;
    const groups = new Map([...ids].sort().map(id => [id, {id, members: [id], degree: 0, adjacent: new Map()}]));
    for (const e of links) {
      total += e.strength;
      degree.set(e.source, degree.get(e.source) + e.strength);
      degree.set(e.target, degree.get(e.target) + e.strength);
      for (const [a,b] of [[e.source,e.target],[e.target,e.source]]) {
        const g = groups.get(a); g.adjacent.set(b, (g.adjacent.get(b) || 0) + e.strength);
      }
    }
    for (const g of groups.values()) g.degree = degree.get(g.id);
    if (total) while (true) {
      let best = null, gain = 1e-10;
      for (const a of groups.values()) for (const [id, weight] of a.adjacent) {
        if (a.id >= id) continue;
        const b = groups.get(id);
        const delta = weight / total - (a.degree / total) * (b.degree / total) / 2;
        if (delta > gain) { gain = delta; best = [a,b]; }
      }
      if (!best) break;
      const [a,b] = best;
      a.members.push(...b.members); a.degree += b.degree; a.adjacent.delete(b.id);
      for (const [id, weight] of b.adjacent) {
        if (id === a.id) continue;
        a.adjacent.set(id, (a.adjacent.get(id) || 0) + weight);
        const neighbor = groups.get(id);
        neighbor.adjacent.delete(b.id); neighbor.adjacent.set(a.id, a.adjacent.get(id));
      }
      groups.delete(b.id);
    }
    const compare = (a,b) => degree.get(b) - degree.get(a) || a.localeCompare(b);
    const clusters = [...groups.values()].filter(g => g.degree > 0).map(g => ({id:g.id, members:g.members.sort(compare)}))
      .sort((a,b) => b.members.length-a.members.length || a.id.localeCompare(b.id));
    return {clusters, isolated: [...ids].filter(id => !degree.get(id)).sort(), degree};
  }
  if (typeof module !== 'undefined') module.exports = {clusterTopics};
  else window.SignalTopicClusters = {clusterTopics};
})();
