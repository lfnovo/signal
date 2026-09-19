const {test} = require('node:test');
const assert = require('node:assert/strict');
const {clusterTopics} = require('../src/signal_inbox/static/topic-clusters.js');
const topics = ids => ids.split('').map(id => ({id}));
const edge = (source,target,strength=1) => ({source,target,strength});
const members = result => result.clusters.map(c=>[...c.members].sort().join('')).sort();
test('dense communities stay distinct across a weak bridge',()=>{
  const links=[edge('a','b'),edge('a','c'),edge('b','c'),edge('d','e'),edge('d','f'),edge('e','f'),edge('c','d',.05)];
  const result=clusterTopics(topics('abcdefz'),links);
  assert.deepEqual(members(result),['abc','def']);
  assert.deepEqual(result.isolated,['z']);
  assert.deepEqual(members(clusterTopics(topics('zfedcba'),[...links].reverse())),members(result));
});
test('all topics are retained once, including disconnected and invalid links',()=>{
  const result=clusterTopics(topics('abcde'),[edge('a','b'),edge('c','d'),edge('d','missing'),edge('e','e'),edge('a','e',0),edge('b','e',NaN)]);
  assert.deepEqual(members(result),['ab','cd']);
  assert.deepEqual(result.isolated,['e']);
  assert.equal(new Set([...result.clusters.flatMap(c=>c.members),...result.isolated]).size,5);
});
test('empty and unconnected catalogs do not fabricate clusters',()=>{
  assert.deepEqual(clusterTopics([],[]).clusters,[]);
  assert.deepEqual(clusterTopics(topics('abc'),[]).isolated,['a','b','c']);
});
test('scope changes recompute communities without mutating catalog or edges',()=>{
  const catalog=topics('abcd'),links=[edge('a','b'),edge('c','d')];
  const before=JSON.stringify({catalog,links});
  assert.deepEqual(members(clusterTopics(catalog,links)),['ab','cd']);
  assert.deepEqual(members(clusterTopics(catalog,[links[0]])),['ab']);
  assert.equal(JSON.stringify({catalog,links}),before);
});
