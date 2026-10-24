let csrf = ''
export class ApiError extends Error {
  constructor(public status: number, public code: string) {
    const messages: Record<number, string> = {
      401: '会话已过期，请重新登录', 403: '当前账号没有此操作权限，或会话验证已过期',
      404: '记录不存在或当前账号不可访问', 409: '记录已更新或提交内容冲突，请刷新后重试',
      422: '提交内容不符合要求，请检查表单', 429: '已达到速率或配额上限，请稍后重试',
      500: '服务发生异常，请稍后重试', 503: '服务暂时不可用，请稍后重试',
    }
    super(`${messages[status] || '请求未完成'}（${status} / ${code}）`)
  }
}
export async function refreshCsrf() {
  const response=await fetch('/api/v2/csrf',{credentials:'same-origin'})
  if(!response.ok)throw new ApiError(response.status,'csrf_unavailable')
  csrf=(await response.json()).token
}
export async function api(path:string, body?:unknown, headers:Record<string,string>={}) {
  const response=await fetch('/api/v2'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',
    headers:{...(body===undefined?{}:{'Content-Type':'application/json','X-XSRF-TOKEN':csrf}),...headers},
    body:body===undefined?undefined:JSON.stringify(body)})
  const data=await response.json().catch(()=>({}))
  if(!response.ok)throw new ApiError(response.status,data.code||'request_failed')
  return data
}
export async function login(username:string,password:string) {
  await refreshCsrf()
  const response=await fetch('/api/v2/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/x-www-form-urlencoded','X-XSRF-TOKEN':csrf},body:new URLSearchParams({username,password})})
  if(!response.ok)throw new Error('登录失败，请检查用户名和密码')
  await refreshCsrf()
}
export async function logout() {
  const response=await fetch('/api/v2/logout',{method:'POST',credentials:'same-origin',headers:{'X-XSRF-TOKEN':csrf}})
  if(!response.ok)throw new ApiError(response.status,'logout_unconfirmed')
  csrf=''
}
