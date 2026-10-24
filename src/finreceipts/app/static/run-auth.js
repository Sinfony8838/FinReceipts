// Credentials live only in this document. Never put them in a URL or storage.
(() => {
  let token = '', identity = null;
  const $ = id => document.getElementById(id);
  function headers(extra = {}) { return {...extra, ...(token ? {Authorization:'Bearer ' + token} : {})}; }
  async function request(url, options = {}) {
    const response = await fetch(url, {...options, headers:headers(options.headers)});
    if (response.status === 401) {
      identity = null;
      if ($('auth-panel')) $('auth-panel').hidden = false;
      window.dispatchEvent(new CustomEvent('fin-auth-required'));
    }
    return response;
  }
  async function connect(value = '') {
    token = value; identity = null;
    const response = await request('/api/session');
    if (!response.ok) throw new Error('请使用当前服务配置的访问令牌认证。');
    identity = await response.json();
    if ($('auth-user')) $('auth-user').textContent = '当前用户：' + identity.principal_id;
    if ($('auth-panel')) $('auth-panel').hidden = identity.auth_mode === 'local';
    return identity;
  }
  window.FinRunAuth = {request, connect, headers, principal:() => identity?.principal_id};
})();
