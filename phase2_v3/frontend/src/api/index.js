import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '/api/v3',
  timeout: 180000,
})

api.interceptors.response.use(
  (res) => res.data,
  (err) => {
    const msg = err.response?.data?.detail || err.message
    console.error('[API Error]', msg)
    return Promise.reject(err)
  }
)

export default {
  // ═══ Run 生命周期管理 ═══
  createRun(formData) {
    return api.post('/runs', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 600000, // Dify 编译链路可能耗时数分钟（LLM推理 + 精修循环）
    })
  },
  listRuns() {
    return api.get('/runs')
  },
  // 预设按钮统一注册表（config/presets.py → GET /presets）
  listPresets() {
    return api.get('/presets')
  },
  getRun(runId) {
    return api.get(`/runs/${runId}`)
  },
  deleteRun(runId) {
    return api.delete(`/runs/${runId}`)
  },
  executeRun(runId, confirmed = true) {
    return api.post(`/runs/${runId}/execute`, { confirmed })
  },
  getRunStatus(runId) {
    return api.get(`/runs/${runId}/status`)
  },
  getDag(runId) {
    return api.get(`/runs/${runId}/dag`)
  },

  // ═══ RAG 知识检索（后端使用 Form 参数，非 JSON body） ═══
  ragSearch(query, topK = 5) {
    const params = new URLSearchParams({ query, top_k: String(topK) })
    return api.post('/rag/search', params)
  },
  ragStatus(light = true) {
    // light 模式毫秒级返回（仅内存索引状态），供徽标/健康检查高频轮询；
    // 需要完整状态（目录扫描 + 新鲜度检查）时传 light=false
    return api.get('/rag/status', { params: light ? { light: true } : {} })
  },
  ragQa(query, history = [], topK = 5) {
    const params = new URLSearchParams({
      query,
      history: JSON.stringify(history),
      top_k: String(topK),
    })
    return api.post('/rag/qa', params)
  },

  // ═══ 多Agent协作管线（复杂问题自动拆解） ═══
  agentPipeline(formData) {
    return api.post('/agent/pipeline', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 600000, // 4个Agent串行推理，最长10分钟
    })
  },

  // ═══ 格式规范化 ═══
  formatNormalize(formData) {
    return api.post('/format/normalize', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  // ═══ 下载 ═══
  getDownloadStatus(taskId) {
    return api.get(`/download/status/${taskId}`)
  },
  getDownloadUrl(filename) {
    return `${api.defaults.baseURL}/download/file/${filename}`
  },
}
