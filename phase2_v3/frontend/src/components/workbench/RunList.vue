<template>
  <div class="card run-list">
    <div class="card-header">
      📋 Run 快照历史
      <button class="btn btn-sm btn-outline" @click="$emit('refresh')">🔄 刷新</button>
    </div>

    <div v-if="loading" class="loading-overlay">
      <span class="spinner"></span> 加载中...
    </div>

    <div v-else-if="!runs.length" class="empty-hint">
      暂无 Run 记录 —— 请从左侧「📂 上传数据文件」并填写「🎯 审计意图」提交编译，<br />
      任务进度与历史快照会显示在这里
    </div>

    <table v-else class="data-table">
      <thead>
        <tr>
          <th>Run ID</th>
          <th>主题</th>
          <th>状态</th>
          <th>时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="run in runs" :key="run.run_id" :class="{ clickable: run.status === 'COMPLETED' || run.status === 'FAILED' }">
          <td class="run-id-cell" :title="run.run_id" @click="clickRun(run)">
            {{ shortId(run.run_id) }}
          </td>
          <td class="subject-cell" :title="run.subject" @click="clickRun(run)">
            {{ run.subject || '-' }}
          </td>
          <td>
            <span class="badge" :class="'badge-' + (run.status || 'queued').toLowerCase()">
              {{ statusText(run.status) }}
            </span>
          </td>
          <td class="time-cell">{{ formatTime(run.created_at) }}</td>
          <td class="action-cell">
            <button
              v-if="run.status === 'PENDING_REVIEW'"
              class="btn btn-sm btn-warning"
              @click="$emit('review', run.run_id)"
            >
              审阅方案
            </button>
            <button
              v-if="run.status === 'COMPLETED' || run.status === 'FAILED'"
              class="btn btn-sm btn-primary"
              @click="$emit('view-detail', run.run_id)"
            >
              查看详情
            </button>
            <button
              class="btn btn-sm btn-outline"
              style="color: #c62828; border-color: #c62828; margin-left: 4px;"
              @click="$emit('delete', run.run_id)"
              title="删除"
            >
              🗑
            </button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup>
defineProps({
  runs: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
})

defineEmits(['view-detail', 'review', 'refresh', 'delete'])

const STATUS_MAP = {
  QUEUED: '排队中',
  COMPILING: '编译中',
  PENDING_REVIEW: '待审批',
  RUNNING: '执行中',
  COMPLETED: '已完成',
  FAILED: '失败',
}

function statusText(s) {
  return STATUS_MAP[s] || s || '未知'
}

function shortId(id) {
  if (!id) return '-'
  const parts = id.split('_')
  if (parts.length >= 3) {
    const last = parts[parts.length - 1]
    return `${parts[0]}_${parts[1]}_..._${last}`
  }
  return id.length > 30 ? id.slice(0, 27) + '...' : id
}

function formatTime(t) {
  if (!t) return '-'
  try {
    const d = new Date(t)
    return d.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return t.slice(0, 16)
  }
}

function clickRun(run) {
  // not emitted here; parent decides via row click or action buttons
}
</script>

<style scoped>
.run-id-cell {
  font-family: 'Courier New', monospace;
  font-size: 12px;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subject-cell {
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.time-cell {
  font-size: 12px;
  color: #888;
  white-space: nowrap;
}

.action-cell {
  white-space: nowrap;
}

.empty-hint {
  text-align: center;
  color: #bbb;
  padding: 30px;
}
</style>
