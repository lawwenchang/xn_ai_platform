<template>
  <div>
    <div class="card">
      <div class="card-header">📋 格式规范化</div>
      <p class="format-desc">
        上传待排版文件（可选带模板），平台将自动按照模板统一段落、字体、表格、页面边距和打印设置。
      </p>

      <!-- 拖拽上传 -->
      <div
        class="drop-zone"
        :class="{ 'drop-active': isDragging }"
        @dragover.prevent="isDragging = true"
        @dragleave.prevent="isDragging = false"
        @drop.prevent="handleDrop"
      >
        <label class="btn btn-outline upload-label">
          <input
            type="file"
            multiple
            accept=".docx,.xlsx,.xls,.doc"
            @change="handleFileSelect"
            style="display: none"
          />
          📎 选择文件
        </label>
        <span class="drop-hint">或拖拽文件到此处（.docx / .xlsx）</span>
      </div>

      <!-- 文件列表 -->
      <ul v-if="files.length" class="file-list">
        <li v-for="(f, i) in files" :key="i" class="file-item">
          <span class="file-icon">{{ fileIcon(f.name) }}</span>
          <span class="file-name">{{ f.name }}</span>
          <span class="file-size">{{ formatSize(f.size) }}</span>
          <label class="template-toggle">
            <input type="radio" :value="i" v-model="templateIndex" />
            作为模板
          </label>
          <button class="btn-remove" @click="removeFile(i)">×</button>
        </li>
      </ul>

      <!-- 操作按钮 -->
      <div class="format-actions">
        <button
          class="btn btn-primary"
          :disabled="files.length === 0 || processing"
          @click="doNormalize"
        >
          <span v-if="processing" class="spinner"></span>
          🪄 一键格式规范化
        </button>
      </div>

      <!-- 结果 -->
      <div v-if="result" class="format-result" :class="result.status === 'success' ? 'alert-success' : 'alert-error'">
        <div v-if="result.status === 'success'">
          <p><strong>模板来源：</strong>{{ result.template }}</p>
          <p><strong>已转换文件数：</strong>{{ result.converted_count }}</p>
          <ul v-if="result.converted_files">
            <li v-for="(f, i) in result.converted_files" :key="i">{{ f }}</li>
          </ul>
          <a v-if="result.download_url" :href="result.download_url" class="btn btn-success btn-sm" style="margin-top: 10px;">
            📥 下载规范化结果
          </a>
        </div>
        <div v-else>
          {{ result.message }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import api from '../../api/index.js'

const files = ref([])
const isDragging = ref(false)
const templateIndex = ref(-1)
const processing = ref(false)
const result = ref(null)

function addFiles(newFiles) {
  const valid = [...newFiles].filter((f) => {
    const ext = '.' + (f.name.split('.').pop() || '').toLowerCase()
    if (!['.docx', '.xlsx', '.xls'].includes(ext)) {
      alert(`不支持的文件格式：${f.name}`)
      return false
    }
    return true
  })
  files.value = [...files.value, ...valid]
}

function handleFileSelect(e) {
  addFiles(e.target.files)
  e.target.value = ''
}

function handleDrop(e) {
  isDragging.value = false
  addFiles(e.dataTransfer.files)
}

function removeFile(i) {
  files.value = files.value.filter((_, idx) => idx !== i)
  if (templateIndex.value === i) templateIndex.value = -1
  else if (templateIndex.value > i) templateIndex.value--
}

function formatSize(bytes) {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB']
  let i = 0
  let s = bytes
  while (s >= 1024 && i < 2) { s /= 1024; i++ }
  return s.toFixed(i === 0 ? 0 : 1) + ' ' + units[i]
}

function fileIcon(name) {
  const ext = (name || '').split('.').pop()?.toLowerCase()
  if (['xlsx', 'xls'].includes(ext)) return '📊'
  if (ext === 'docx') return '📝'
  return '📄'
}

async function doNormalize() {
  if (!files.value.length) return
  processing.value = true
  result.value = null
  try {
    const fd = new FormData()
    for (const f of files.value) {
      fd.append('files', f)
    }
    if (templateIndex.value >= 0 && templateIndex.value < files.value.length) {
      fd.append('template_index', String(templateIndex.value))
    }
    fd.append('output_format', 'auto')
    const res = await api.formatNormalize(fd)
    result.value = res
  } catch (err) {
    result.value = {
      status: 'error',
      message: '规范化失败：' + (err.response?.data?.detail || err.message),
    }
  } finally {
    processing.value = false
  }
}
</script>

<style scoped>
.format-desc {
  color: #666;
  font-size: 13px;
  margin-bottom: 16px;
  line-height: 1.6;
}

.drop-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 24px;
  border: 2px dashed #d0d0d0;
  border-radius: 8px;
  background: #fafafa;
  margin-bottom: 12px;
}

.drop-zone.drop-active {
  border-color: #1a237e;
  background: #e8eaf6;
}

.drop-hint {
  font-size: 12px;
  color: #999;
}

.file-list {
  list-style: none;
  margin-bottom: 12px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  background: #f5f6fa;
  border-radius: 6px;
  margin-bottom: 4px;
  font-size: 13px;
}

.file-icon { font-size: 16px; }

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size { color: #999; font-size: 11px; }

.template-toggle {
  font-size: 12px;
  color: #1a237e;
  cursor: pointer;
  white-space: nowrap;
  display: flex;
  align-items: center;
  gap: 4px;
}

.btn-remove {
  background: none;
  border: none;
  color: #c62828;
  font-size: 18px;
  cursor: pointer;
}

.format-actions {
  margin-bottom: 16px;
}

.format-result {
  margin-top: 12px;
}
</style>
