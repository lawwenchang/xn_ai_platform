<template>
  <div class="card rag-search">
    <div class="card-header">
      🧠 知识问答（RAG）
      <button v-if="messages.length" class="btn-clear" @click="clearChat">清空对话</button>
    </div>

    <div ref="chatBox" class="chat-box">
      <div v-if="!messages.length" class="empty-hint">
        可提问审计准则、法规或平台操作，例如：<br />
        "有被审计单位的合同，如何生成审计报告？"
      </div>
      <div v-for="(m, i) in messages" :key="i" class="msg" :class="m.role">
        <div class="bubble">
          <div class="msg-text">{{ m.content }}</div>
          <div v-if="m.role === 'assistant' && m.engine === 'retrieval_only'" class="engine-note">
            ⚠️ AI 离线，仅返回知识库原文片段
          </div>
          <details v-if="m.sources && m.sources.length" class="sources">
            <summary>📚 依据来源（{{ m.sources.length }}）</summary>
            <div v-for="(s, j) in m.sources" :key="j" class="source-item">
              <div class="source-title">
                {{ s.source || '文档片段 ' + (j + 1) }}
                <span v-if="s.score" class="source-score">{{ (s.score * 100).toFixed(0) }}%</span>
              </div>
              <div class="source-snippet">{{ s.text }}</div>
            </div>
          </details>
        </div>
      </div>
      <div v-if="asking" class="msg assistant">
        <div class="bubble typing">思考中...</div>
      </div>
    </div>

    <div class="search-row">
      <input
        v-model="query"
        placeholder="输入问题，支持连续追问..."
        :disabled="asking"
        @keydown.enter="ask"
      />
      <button class="btn btn-sm btn-primary" :disabled="!query.trim() || asking" @click="ask">
        {{ asking ? '...' : '发送' }}
      </button>
    </div>

    <div class="rag-footer">
      <span
        class="rag-status"
        :class="indexReady === null ? 'checking' : indexReady ? 'online' : 'offline'"
        title="点击重新检测知识库状态"
        @click="checkStatus(true)"
      >
        {{ indexReady === null ? '状态检查中…' : indexReady ? '知识库就绪' : '知识库未就绪' }}
      </span>
    </div>
  </div>
</template>

<script setup>
import { ref, nextTick, onMounted } from 'vue'
import api from '../../api/index.js'
import { useRagStatus } from '../../composables/useRagStatus.js'

const query = ref('')
const messages = ref([])
const asking = ref(false)
const chatBox = ref(null)

// 知识库状态：模块级共享单例（路由切换不重置，避免徽标闪「未就绪」）
const { indexReady, checkStatus } = useRagStatus()

async function ask() {
  const q = query.value.trim()
  if (!q || asking.value) return
  messages.value.push({ role: 'user', content: q })
  query.value = ''
  asking.value = true
  scrollBottom()
  try {
    // 携带最近 6 条对话历史，支持多轮追问
    const history = messages.value
      .slice(0, -1)
      .slice(-6)
      .map((m) => ({ role: m.role, content: m.content }))
    const res = await api.ragQa(q, history, 5)
    const d = (res && res.data) || {}
    const answer = (d.answer || '').trim()
    messages.value.push({
      role: 'assistant',
      content:
        answer ||
        (d.sources && d.sources.length
          ? '未能生成回答，以下为知识库相关片段：'
          : '知识库中未找到相关内容，请换个问法，或先重建知识库索引。'),
      sources: d.sources || [],
      engine: d.engine || 'retrieval_only',
    })
  } catch (err) {
    messages.value.push({
      role: 'assistant',
      content: '问答服务异常：' + (err.response?.data?.detail || err.message),
      sources: [],
    })
  } finally {
    asking.value = false
    scrollBottom()
  }
}

function clearChat() {
  messages.value = []
}

async function scrollBottom() {
  await nextTick()
  if (chatBox.value) chatBox.value.scrollTop = chatBox.value.scrollHeight
}

onMounted(checkStatus)
</script>

<style scoped>
.search-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.search-row input {
  flex: 1;
}

.chat-box {
  max-height: 420px;
  min-height: 120px;
  overflow-y: auto;
  padding: 4px 2px;
  margin-bottom: 10px;
}

.msg {
  display: flex;
  margin-bottom: 10px;
}

.msg.user {
  justify-content: flex-end;
}

.msg.assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 92%;
  padding: 8px 12px;
  border-radius: 10px;
  font-size: 13px;
  line-height: 1.6;
}

.msg.user .bubble {
  background: #1a237e;
  color: #fff;
  border-bottom-right-radius: 2px;
}

.msg.assistant .bubble {
  background: #f5f6fa;
  color: #333;
  border-bottom-left-radius: 2px;
}

.msg-text {
  white-space: pre-wrap;
  word-break: break-word;
}

.engine-note {
  margin-top: 6px;
  font-size: 11px;
  color: #e65100;
}

.sources {
  margin-top: 8px;
  border-top: 1px dashed #ddd;
  padding-top: 6px;
}

.sources summary {
  font-size: 12px;
  color: #1a237e;
  cursor: pointer;
}

.source-item {
  padding: 6px 0;
  border-bottom: 1px solid #eee;
}

.source-title {
  font-weight: 600;
  font-size: 12px;
  color: #37474f;
  margin-bottom: 2px;
}

.source-score {
  font-weight: 400;
  font-size: 11px;
  color: #999;
  margin-left: 6px;
}

.source-snippet {
  font-size: 12px;
  color: #666;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.typing {
  color: #999;
  font-style: italic;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.btn-clear {
  border: none;
  background: transparent;
  color: #90a4ae;
  font-size: 12px;
  cursor: pointer;
}

.btn-clear:hover {
  color: #c62828;
}

.rag-footer {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid #eee;
  text-align: right;
}

.rag-status {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 10px;
  cursor: pointer;
}

.rag-status.checking {
  color: #78909c;
  background: #eceff1;
}

.rag-status.online {
  color: #2e7d32;
  background: #e8f5e9;
}

.rag-status.offline {
  color: #c62828;
  background: #ffebee;
}

.empty-hint {
  text-align: center;
  color: #bbb;
  padding: 20px;
  font-size: 13px;
}
</style>
