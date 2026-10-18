const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../src/finreceipts/app/static/create-journal.js'), 'utf8');
const key = 'finreceipts.pending-create.v1';
const record = {version:1, key:'12345678-1234-1234-1234-123456789abc', body:{
  question:'Apple revenue?', ticker:'AAPL', fiscal_years:[2023], max_dispatches:6, max_revisions:2
}};
function load(overrides={}) {
  const data = new Map();
  const storage = {getItem:k=>data.get(k) ?? null, setItem:(k,v)=>data.set(k,v), removeItem:k=>data.delete(k), ...overrides};
  const context = {window:{}, sessionStorage:storage};
  vm.runInNewContext(source, context);
  return {journal:context.window.FinCreateJournal, storage, data};
}
test('pending key and exact body survive script reload and clear only explicitly',()=>{
  const {journal,storage,data}=load();
  assert.equal(journal.load(),null);
  journal.save(record);
  const reloaded=load(storage).journal;
  assert.equal(JSON.stringify(reloaded.load()),JSON.stringify(record));
  assert.equal(data.size,1);
  reloaded.clear();
  assert.equal(journal.load(),null);
});
for (const [name, raw] of [
  ['bad JSON','{'], ['null','null'], ['unsupported version',JSON.stringify({...record,version:2})],
  ['missing key',JSON.stringify({...record,key:null})],
  ['malformed body',JSON.stringify({...record,body:{...record.body,fiscal_years:'2023'}})],
  ['out of range budget',JSON.stringify({...record,body:{...record.body,max_dispatches:0}})],
  ['unknown body property',JSON.stringify({...record,body:{...record.body,extra:true}})],
  ['oversized record','x'.repeat(50001)],
]) test(name+' cannot silently create a replacement key',()=>{
  const {journal,data}=load(); data.set(key,raw);
  assert.throws(()=>journal.load(),e=>e.code==='creation_record_invalid');
  assert.equal(data.get(key),raw);
  journal.clear(); assert.equal(journal.load(),null);
});
for (const method of ['getItem','setItem','removeItem']) test('storage '+method+' exceptions are safe failures',()=>{
  const {journal}=load({[method]:()=>{throw new Error('private storage detail');}});
  const action=method==='getItem'?()=>journal.load():method==='setItem'?()=>journal.save(record):()=>journal.clear();
  assert.throws(action,e=>e.code==='creation_storage_unavailable'&&!e.message.includes('private'));
});
test('silent failure to persist is detected before dispatch can proceed',()=>{
  const {journal}=load({setItem:()=>{}});
  assert.throws(()=>journal.save(record),e=>e.code==='creation_storage_unavailable');
});
test('silent clear failure retains the pending request',()=>{
  const {journal,data}=load({removeItem:()=>{}}); journal.save(record);
  assert.throws(()=>journal.clear(),e=>e.code==='creation_storage_unavailable');
  assert.equal(data.get(key),JSON.stringify(record));
});
test('invalid client input is rejected without overwriting a pending record',()=>{
  const {journal,data}=load(); journal.save(record);
  assert.throws(()=>journal.save({...record,body:{...record.body,max_revisions:6}}),e=>e.code==='invalid_request');
  assert.equal(data.get(key),JSON.stringify(record));
});
