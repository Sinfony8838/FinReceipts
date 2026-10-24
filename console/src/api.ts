let csrf = ''
export async function refreshCsrf() {
  const response=await fetch('/api/v2/csrf',{credentials:'same-origin'})
  if(!response.ok)throw new Error('服务暂时不可用')
  csrf=(await response.json()).token
}
export async function api(path:string, body?:unknown, headers:Record<string,string>={}) {
  const response=await fetch('/api/v2'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',
    headers:{...(body===undefined?{}:{'Content-Type':'application/json','X-XSRF-TOKEN':csrf}),...headers},
    body:body===undefined?undefined:JSON.stringify(body)})
  const data=await response.json().catch(()=>({}))
  if(!response.ok)throw new Error(`${response.status} · ${data.code||'request_failed'}`)
  return data
}
export async function login(username:string,password:string) {
  await refreshCsrf()
  const response=await fetch('/api/v2/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded','X-XSRF-TOKEN':csrf},body:new URLSearchParams({username,password})})
  if(!response.ok)throw new Error('登录失败，请检查用户名和密码')
  await refreshCsrf()
}
export async function logout() {
  await fetch('/api/v2/logout',{method:'POST',credentials:'same-origin',headers:{'X-XSRF-TOKEN':csrf}})
}
