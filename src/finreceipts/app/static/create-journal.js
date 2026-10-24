// One unresolved creation per browser tab. Persist BEFORE sending the request.
(() => {
  const name = 'finreceipts.pending-create.v1';
  const fields = ['question', 'ticker', 'fiscal_years', 'max_dispatches', 'max_revisions'];
  function failure(code) { const error = new Error(code); error.code = code; return error; }
  function valid(record) {
    const b = record?.body;
    const scoped = record?.version === 2 && /^[A-Za-z0-9_.-]{1,64}$/.test(record.principal_id || '') && record.operation === 'create_run';
    const target = b?.target;
    const targetValid = target && typeof target === 'object' && ['direct','ratio','yoy_growth'].includes(target.kind) &&
      typeof target.metric === 'string' && Number.isInteger(target.fiscal_year) &&
      b.fiscal_years?.length === 1 && b.fiscal_years[0] === target.fiscal_year &&
      Object.keys(target).every(k => ['kind','metric','fiscal_year','denominator'].includes(k)) &&
      (target.kind === 'ratio' ? typeof target.denominator === 'string' : !Object.hasOwn(target,'denominator'));
    const extra = record?.version === 2 && Object.hasOwn(b || {}, 'target') ? ['target','use_tools','as_of'] : [];
    return (record?.version === 1 || scoped) && /^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(record.key || '') &&
      b && !Array.isArray(b) && Object.keys(b).length === fields.length + extra.length && fields.concat(extra).every(k => Object.hasOwn(b, k)) &&
      (!extra.length || (targetValid && b.use_tools === true && (b.as_of === null || /^\d{4}-\d{2}-\d{2}$/.test(b.as_of)))) &&
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
  function forPrincipal(record, principal) {
    if (!record) return null;
    if ((record.version === 1 && principal !== 'local') || (record.version === 2 && record.principal_id !== principal)) {
      throw failure('creation_identity_mismatch');
    }
    return record;
  }
  window.FinCreateJournal = {load, save, clear, forPrincipal};
})();
