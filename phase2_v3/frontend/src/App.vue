<template>
  <div id="app-root">
    <TopBar />
    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import TopBar from './components/layout/TopBar.vue'
</script>

<style scoped>
#app-root {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f5f6fa;
}

.main-content {
  flex: 1;
  overflow: auto;
}
</style>
