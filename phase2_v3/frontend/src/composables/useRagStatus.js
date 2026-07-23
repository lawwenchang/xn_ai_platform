import { ref } from 'vue'
import api from '../api/index.js'

// ═══ 模块级单例状态 ═══
// 路由切换导致组件销毁重建时，该状态不会丢失，
// 避免每次回到工作台「知识库就绪」徽标都从红色「未就绪」闪回。
const indexReady = ref(null) // null=检查中 / true=就绪 / false=未就绪
let lastCheckAt = 0
let inflight = null

const CACHE_MS = 30000 // 30s 内复用上次结果，避免高频重复请求

async function checkStatus(force = false) {
  const now = Date.now()
  if (!force && indexReady.value !== null && now - lastCheckAt < CACHE_MS) {
    return indexReady.value
  }
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const res = await api.ragStatus() // 轻量模式，毫秒级返回
      const d = res && res.success ? res.data : null
      indexReady.value = d ? (d.ready !== undefined ? !!d.ready : true) : false
    } catch {
      indexReady.value = false
    } finally {
      lastCheckAt = Date.now()
      inflight = null
    }
    return indexReady.value
  })()
  return inflight
}

export function useRagStatus() {
  return { indexReady, checkStatus }
}
