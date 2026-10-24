const labels: Record<string, string> = {
  QUEUED: '等待调度', READY: '等待执行', RUNNING: '正在执行', COMPLETED: '执行完成',
  FAILED: '执行失败', UNKNOWN: '调用结果未知', CANCELLED: '已取消', PENDING: '待处理',
  SUPPORTED: '证据支持', CONTRADICTED: '与证据矛盾', UNSUPPORTED: '缺少支持',
  ACCEPTED: '已接受', REJECTED: '已驳回', PREPARE: '准备证据', MODEL: '模型执行',
  VERIFY: '确定性核验', DONE: '完成', ADMIN: '管理员', ANALYST: '研究员', REVIEWER: '复核员',
}
const metrics: Record<string, string> = {
  revenue: '营业收入', 'net income': '净利润', 'operating income': '营业利润',
  'gross profit': '毛利润', 'total assets': '总资产', 'total liabilities': '总负债',
  'cash and cash equivalents': '现金及现金等价物',
  'net cash provided by operating activities': '经营活动现金流量净额',
  'diluted EPS': '稀释每股收益', 'research and development expense': '研发费用',
}
export const text = (value: string) => labels[value] || value || '待处理'
export const metricLabel = (value: string) => metrics[value] || value
export const money = (micros: number, digits = 3) =>
  '¥ ' + (Number(micros || 0) / 1e6).toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
export function sourceLink(value: string) {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && url.hostname === 'www.sec.gov' ? url.href : ''
  } catch { return '' }
}
