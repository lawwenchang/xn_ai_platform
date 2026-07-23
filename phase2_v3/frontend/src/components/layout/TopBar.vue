<template>
  <header class="topbar">
    <div class="topbar-left">
      <span class="logo">⚖️</span>
      <h1 class="title">智能审计大脑协同平台</h1>
      <span class="version">v3.0</span>
    </div>
    <nav class="topbar-nav">
      <router-link
        to="/"
        class="nav-link"
        active-class="active"
        exact
        title="数据作业流水线：上传数据 → 提交审计意图 → 编译审批 → 沙箱执行 → 产出底稿/报告文件"
      >
        🏠 工作台
      </router-link>
      <router-link
        to="/agent"
        class="nav-link"
        active-class="active"
        title="多Agent深度分析：问题抽取 → 逻辑推理 → 法规检索 → 报告撰写（产出分析结论，不执行数据处理）"
      >
        🤖 智能分析
      </router-link>
    </nav>
    <div class="topbar-right">
      <span class="status-dot" :class="apiOnline ? 'online' : 'offline'"></span>
      <span class="status-text">{{ apiOnline ? '后端在线' : '后端离线' }}</span>
    </div>
  </header>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import api from '../../api/index.js'

const apiOnline = ref(false)

async function checkHealth() {
  try {
    await api.ragStatus()
    apiOnline.value = true
  } catch {
    apiOnline.value = false
  }
}

onMounted(() => {
  checkHealth()
  setInterval(checkHealth, 30000)
})
</script>

<style scoped>
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  height: 56px;
  background: linear-gradient(135deg, #1a237e, #283593);
  color: #fff;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  flex-shrink: 0;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.logo {
  font-size: 24px;
}

.title {
  font-size: 18px;
  font-weight: 600;
  margin: 0;
}

.version {
  font-size: 12px;
  opacity: 0.7;
  background: rgba(255, 255, 255, 0.15);
  padding: 2px 8px;
  border-radius: 10px;
}

.topbar-nav {
  display: flex;
  gap: 8px;
}

.nav-link {
  color: rgba(255, 255, 255, 0.75);
  text-decoration: none;
  padding: 6px 16px;
  border-radius: 6px;
  font-size: 14px;
  transition: all 0.2s;
}

.nav-link:hover,
.nav-link.active {
  color: #fff;
  background: rgba(255, 255, 255, 0.15);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot.online {
  background: #4caf50;
  box-shadow: 0 0 6px #4caf50;
}

.status-dot.offline {
  background: #f44336;
  box-shadow: 0 0 6px #f44336;
}

.status-text {
  opacity: 0.8;
}
</style>
