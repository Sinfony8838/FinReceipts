// One unresolved creation per browser tab. Persist BEFORE sending the request.
(() => {
  const name = 'finreceipts.pending-create.v1';
  const fields = ['question', 'ticker', 'fiscal_years', 'max_dispatches', 'max_revisions'];
  function failure(code) { const error = new Error(code); error.code = code; return error; }
  function valid(record) {
    const b = record?.body;
    return record?.version === 1 && /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(record.key || '') &&
      b && !Array.isArray(b) && Object.keys(b).length === fields.length && fields.every(k => Object.hasOwn(b, k)) &&
      typeof b.question === 'string' && b.question.length > 0 && b.question.length <= 12000 &&
      typeof b.ticker === 'string' && b.ticker.length > 0 && b.ticker.length <= 16 &&
      Array.isArray(b.fiscal_years) && b.fiscal_years.length > 0 && b.fiscal_years.length <= 10 && b.fiscal_years.every(Number.isInteger) &&
      Number.isInteger(b.max_dispatches) && b.max_dispatches >= 1 && b.max_dispatches <= 50 &&
      Number.isInteger(b.max_revisions) && b.max_revisions >= 0 && b.max_revisions <= 5;
  }
  function access(fn) {
    try { return fn(); } catch (error) {
      if (error.code === 'creation_record_invalid') throw error;
      throw failure('creation_storage_unavailable');
    }
  }
  function load() {
    const raw = access(() => sessionStorage.getItem(name));
    if (raw === null) return null;
    let record;
    try { if (raw.length > 50000) throw new Error(); record = JSON.parse(raw); }
    catch (_) { throw failure('creation_record_invalid'); }
    if (!valid(record)) throw failure('creation_record_invalid');
    return record;
  }
  function save(record) {
    if (!valid(record)) throw failure('invalid_request');
    const raw = JSON.stringify(record);
    access(() => {
      sessionStorage.setItem(name, raw);
      if (sessionStorage.getItem(name) !== raw) throw new Error();
    });
  }
  function clear() {
    access(() => {
      sessionStorage.removeItem(name);
      if (sessionStorage.getItem(name) !== null) throw new Error();
    });
  }
  window.FinCreateJournal = {load, save, clear};
})();
