<template>
  <div class="card intent-input">
    <div class="card-header">🎯 审计意图</div>

    <!-- 预设按钮 -->
    <div class="preset-buttons">
      <button
        v-for="p in presets"
        :key="p.value"
        class="btn btn-sm"
        :class="presetButton === p.value ? 'btn-primary' : 'btn-outline'"
        @click="presetButton = presetButton === p.value ? '' : p.value"
      >
        {{ p.icon }} {{ p.label }}
      </button>
    </div>

    <!-- 意图输入 -->
    <textarea
      v-model="intent"
      placeholder="用自然语言描述审计目标，例如：&#10;「核对医保回款，差异控制在5万以内」&#10;「筛选余额大于50万的应收款，生成函证」&#10;「根据已审底稿生成报告正文和附注」&#10;「帮我校对这份报告的勾稽和措辞」"
      class="intent-textarea"
    ></textarea>

    <!-- 项目编号 & 主题 -->
    <div class="meta-row">
      <input v-model="projectCode" placeholder="项目编号（默认 PROJ_2026_001）" />
      <input v-model="subject" placeholder="审计主题（可选）" />
    </div>

    <button
      class="btn btn-primary submit-btn"
      :disabled="!intent.trim() || loading"
      @click="$emit('submit', { intent: intent.trim(), projectCode, subject, presetButton })"
    >
      <span v-if="loading" class="spinner"></span>
      <span>{{ loading ? '编译中...' : '🚀 提交编译' }}</span>
    </button>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api'

defineProps({
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['submit'])

const intent = ref('')
const projectCode = ref('PROJ_2026_001')
const subject = ref('')
const presetButton = ref('')

// 预设按钮统一注册表（单一事实来源 config/presets.py → GET /api/v3/presets）
// 每个 preset.value 与后端注册表 key 严格一致，确保 Dify 工作流正确路由；
// 网络失败时降级为静态列表（与注册表同步维护）
const FALLBACK_PRESETS = [
  { icon: '🏦', label: '银行流水对账', value: '银行对账', dag: true },
  { icon: '🔗', label: '数据比对与核对', value: '数据比对', dag: true },
  { icon: '🎯', label: '提取式核对', value: '提取式核对', dag: true },
  { icon: '💰', label: '大额交易筛查', value: '大额交易筛查', dag: true },
  { icon: '🔍', label: '智能筛选与抽样', value: '智能筛选', dag: true },
  { icon: '📄', label: '报告与函证生成', value: '文档生成', dag: true },
  { icon: '📑', label: '跨文件对比', value: '跨文件对比', dag: true },
  { icon: '✨', label: '格式规范与纠错', value: '格式与纠错', dag: false, special_route: 'format_normalize' },
]
const presets = ref(FALLBACK_PRESETS)

onMounted(async () => {
  try {
    const res = await api.listPresets()
    if (res.data?.success && res.data.presets?.length) {
      presets.value = res.data.presets.map(p => ({
        icon: p.icon, label: p.label, value: p.value,
        dag: p.dag, special_route: p.special_route || '',
      }))
    }
  } catch (e) {
    // 降级静态列表，不影响使用
    console.warn('预设注册表拉取失败，使用静态降级列表', e)
  }
})
</script>

<style scoped>
.preset-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}

.intent-textarea {
  min-height: 100px;
  margin-bottom: 10px;
  font-size: 14px;
  line-height: 1.6;
}

.meta-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}

.meta-row input {
  flex: 1;
  font-size: 13px;
}

.submit-btn {
  width: 100%;
  padding: 10px;
  font-size: 15px;
}
</style>
