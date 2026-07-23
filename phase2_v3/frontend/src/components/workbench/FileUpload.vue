<template>
  <div class="card file-upload">
    <div class="card-header">
      📂 上传数据文件
      <span v-if="files.length" class="file-count">{{ files.length }} 个文件</span>
    </div>

    <!-- 拖拽区域 -->
    <div
      class="drop-zone"
      :class="{ 'drop-active': isDragging }"
      @dragover.prevent="isDragging = true"
      @dragleave.prevent="isDragging = false"
      @drop.prevent="handleDrop"
    >
      <label class="upload-btn btn btn-outline">
        <input
          type="file"
          multiple
          accept=".xlsx,.xls,.csv,.zip,.rar,.7z"
          @change="handleFileSelect"
          style="display: none"
        />
        📎 选择文件
      </label>
      <span class="drop-hint">或拖拽文件到此处</span>
    </div>

    <!-- 文件列表 -->
    <ul v-if="files.length" class="file-list">
      <li v-for="(f, i) in files" :key="i" class="file-item">
        <span class="file-icon">{{ fileIcon(f.name) }}</span>
        <span class="file-name" :title="f.name">{{ f.name }}</span>
        <span class="file-size">{{ formatSize(f.size) }}</span>
        <button class="btn-remove" @click="removeFile(i)" title="移除">×</button>
      </li>
    </ul>
    <div v-else class="empty-hint">支持 .xlsx .xls .csv .docx .doc .zip .rar .7z（≤100MB）</div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useVModel } from '../../composables/useVModel.js'

const props = defineProps({ modelValue: { type: Array, default: () => [] } })
const emit = defineEmits(['update:modelValue'])
const files = useVModel(props, emit)

const isDragging = ref(false)

const ALLOWED = ['.xlsx', '.xls', '.csv', '.zip', '.rar', '.7z']

function isValidExt(name) {
  const ext = '.' + (name.split('.').pop() || '').toLowerCase()
  return ALLOWED.includes(ext)
}

function addFiles(newFiles) {
  const valid = [...newFiles].filter((f) => {
    if (!isValidExt(f.name)) {
      alert(`不支持的文件类型：${f.name}`)
      return false
    }
    if (f.size > 100 * 1024 * 1024) {
      alert(`文件过大：${f.name}（最大 100MB）`)
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

function removeFile(index) {
  files.value = files.value.filter((_, i) => i !== index)
}

function formatSize(bytes) {
  if (!bytes) return ''
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let size = bytes
  while (size >= 1024 && i < units.length - 1) {
    size /= 1024
    i++
  }
  return size.toFixed(i === 0 ? 0 : 1) + ' ' + units[i]
}

function fileIcon(name) {
  const ext = (name.split('.').pop() || '').toLowerCase()
  if (['xlsx', 'xls'].includes(ext)) return '📊'
  if (ext === 'csv') return '📋'
  if (['zip', 'rar', '7z'].includes(ext)) return '📦'
  return '📄'
}
</script>

<style scoped>
.drop-zone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px;
  border: 2px dashed #d0d0d0;
  border-radius: 8px;
  background: #fafafa;
  transition: all 0.2s;
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
  margin-top: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #f5f6fa;
  margin-bottom: 4px;
  font-size: 13px;
}

.file-icon {
  font-size: 16px;
  flex-shrink: 0;
}

.file-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-size {
  color: #999;
  font-size: 11px;
  flex-shrink: 0;
}

.btn-remove {
  background: none;
  border: none;
  color: #c62828;
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
  flex-shrink: 0;
}

.btn-remove:hover {
  color: #b71c1c;
}

.empty-hint {
  text-align: center;
  color: #bbb;
  font-size: 12px;
  margin-top: 8px;
}

.file-count {
  font-size: 12px;
  color: #1a237e;
  background: #e8eaf6;
  padding: 2px 8px;
  border-radius: 10px;
}
</style>
