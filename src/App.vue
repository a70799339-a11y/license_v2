<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import MainLayout from './layouts/MainLayout.vue'

const route = useRoute()
const isLogin = computed(() => route.path === '/login')
</script>

<template>
  <MainLayout v-if="!isLogin" />
  <router-view v-else />
</template>
