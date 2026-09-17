<template>
  <div class="kb-page">
    <!-- 故障类型管理 -->
    <el-card class="card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>故障类型管理</span>
          <el-button type="primary" size="small" @click="openTypeDialog(null)">
            <el-icon><Plus /></el-icon> 新增类型
          </el-button>
        </div>
      </template>
      <el-table :data="faultTypes" size="small" v-loading="typeLoading">
        <el-table-column prop="name" label="故障类型" />
        <el-table-column prop="description" label="描述" show-overflow-tooltip />
        <el-table-column prop="color_tag" label="级别" width="80">
          <template #default="{ row }">
            <el-tag :type="tagType(row.color_tag)" size="small">{{ row.color_tag }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button size="small" @click="openTypeDialog(row)">编辑</el-button>
            <el-popconfirm title="确认删除？" @confirm="deleteType(row.id)">
              <template #reference>
                <el-button size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <!-- 日志上传 -->
    <el-card class="card" shadow="never">
      <template #header>
        <div class="card-header">
          <span>日志管理</span>
          <div class="header-actions">
            <el-select v-model="filterTypeId" placeholder="按故障类型筛选" clearable size="small" style="width:180px" @change="loadLogs">
              <el-option v-for="t in faultTypes" :key="t.id" :label="t.name" :value="t.id" />
            </el-select>
            <el-button size="small" @click="showUpload = true">
              <el-icon><Upload /></el-icon> 上传日志
            </el-button>
            <el-button size="small" type="primary" @click="showCase = true">
              <el-icon><Plus /></el-icon> 新增知识案例
            </el-button>
            <el-button size="small" type="warning" @click="openImport">
              从标注典型案例导入
            </el-button>
            <el-button size="small" type="success" @click="doBatchIndex">一键入库</el-button>
          </div>
        </div>
      </template>
      <el-table :data="logs" size="small" v-loading="logLoading">
        <el-table-column prop="filename" label="文件名" show-overflow-tooltip />
        <el-table-column prop="fault_type_name" label="故障类型" width="130" />
        <el-table-column prop="summary" label="备注" show-overflow-tooltip />
        <el-table-column prop="is_indexed" label="已向量化" width="100">
          <template #default="{ row }">
            <el-tag :type="row.is_indexed ? 'success' : 'info'" size="small">
              {{ row.is_indexed ? '是' : '否' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="上传时间" width="170">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="80">
          <template #default="{ row }">
            <el-popconfirm title="确认删除？" @confirm="deleteLogItem(row.id)">
              <template #reference>
                <el-button size="small" type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination">
        <el-pagination
          v-model:current-page="logPage"
          :page-size="20"
          :total="logTotal"
          layout="total, prev, pager, next"
          @current-change="loadLogs"
          small
        />
      </div>
    </el-card>

    <!-- 故障类型弹窗 -->
    <el-dialog v-model="typeDialogVisible" :title="editingType ? '编辑故障类型' : '新增故障类型'" width="420px">
      <el-form :model="typeForm" label-width="90px">
        <el-form-item label="类型名称">
          <el-input v-model="typeForm.name" placeholder="如：内存溢出" />
        </el-form-item>
        <el-form-item label="描述">
          <el-input v-model="typeForm.description" type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="严重级别">
          <el-select v-model="typeForm.color_tag">
            <el-option label="红色（高危）" value="red" />
            <el-option label="黄色（中危）" value="yellow" />
            <el-option label="绿色（低危）" value="green" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="typeDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submitType">确认</el-button>
      </template>
    </el-dialog>

    <!-- 上传日志弹窗 -->
    <el-dialog v-model="showUpload" title="上传日志文件" width="460px">
      <el-form label-width="90px">
        <el-form-item label="故障类型">
          <el-select v-model="uploadTypeId" placeholder="请选择故障类型" style="width:100%">
            <el-option v-for="t in faultTypes" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="uploadSummary" placeholder="可选备注" />
        </el-form-item>
        <el-form-item label="日志文件">
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            accept=".log,.txt,.csv,.json"
            :on-change="onFileChange"
          >
            <el-button size="small">选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">支持 .log .txt .csv .json，最大 10MB</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showUpload = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="doUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- 新增知识案例弹窗 -->
    <el-dialog v-model="showCase" title="新增知识案例" width="520px">
      <el-form label-width="90px">
        <el-form-item label="故障类型">
          <el-select v-model="caseForm.fault_type_id" placeholder="请选择故障类型" style="width:100%">
            <el-option v-for="t in faultTypes" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="根因">
          <el-input v-model="caseForm.root_cause" type="textarea" :rows="2" placeholder="该故障的根本原因（必填）" />
        </el-form-item>
        <el-form-item label="解决方案">
          <el-input v-model="caseForm.solution" type="textarea" :rows="2" placeholder="处理/恢复方案（可选）" />
        </el-form-item>
        <el-form-item label="样例日志">
          <el-input v-model="caseForm.sample_log" type="textarea" :rows="4" placeholder="可粘贴一段代表性日志片段（可选），用于提升检索匹配度" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCase = false">取消</el-button>
        <el-button type="primary" :loading="caseSaving" @click="doCreateCase">保存并入库</el-button>
      </template>
    </el-dialog>

    <!-- 从标注典型案例导入 -->
    <el-dialog v-model="showImport" title="从标注典型案例导入（入知识库 + 知识图谱）" width="820px">
      <div style="display:flex; gap:8px; margin-bottom:12px;">
        <el-input v-model="importAnomalyType" placeholder="按故障类型(anomaly_type)过滤" clearable style="max-width:260px" @keyup.enter="loadAnnotationCases" />
        <el-button @click="loadAnnotationCases" :loading="importLoading">查询</el-button>
      </div>
      <el-table :data="annotationCases" height="360" v-loading="importLoading" @selection-change="onImportSelect" row-key="_key">
        <el-table-column type="selection" width="46" />
        <el-table-column prop="anomaly_type" label="故障类型" width="150" show-overflow-tooltip />
        <el-table-column prop="package_name" label="来源数据包" width="150" show-overflow-tooltip />
        <el-table-column prop="note" label="标注备注" width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column label="日志预览" show-overflow-tooltip>
          <template #default="{ row }">{{ (row.log_text || '').slice(0, 80) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!importLoading && !annotationCases.length" description="标注子系统暂无异常标注案例，或不可达" />
      <template #footer>
        <span style="margin-right:auto; color:#94a3b8; font-size:12px;">已选 {{ selectedCases.length }} 条</span>
        <el-button @click="showImport = false">取消</el-button>
        <el-button type="primary" :disabled="!selectedCases.length" :loading="importSubmitting" @click="doImportCases">导入所选</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { listFaultTypes, createFaultType, updateFaultType, deleteFaultType, listLogs, uploadLog, deleteLog, batchIndex, createKnowledgeCase, listAnnotationCases, importAnnotationCases } from '@/api/knowledgeBase'

const faultTypes = ref([])
const typeLoading = ref(false)
const typeDialogVisible = ref(false)
const editingType = ref(null)
const typeForm = ref({ name: '', description: '', color_tag: 'red' })

const logs = ref([])
const logLoading = ref(false)
const logPage = ref(1)
const logTotal = ref(0)
const filterTypeId = ref(null)

const showUpload = ref(false)
const uploadTypeId = ref(null)
const uploadSummary = ref('')
const selectedFile = ref(null)
const uploading = ref(false)

// 新增知识案例
const showCase = ref(false)
const caseSaving = ref(false)
const caseForm = ref({ fault_type_id: null, root_cause: '', solution: '', sample_log: '' })

// 从标注典型案例导入
const showImport = ref(false)
const importLoading = ref(false)
const importSubmitting = ref(false)
const annotationCases = ref([])
const selectedCases = ref([])
const importAnomalyType = ref('')

async function openImport() {
  showImport.value = true
  selectedCases.value = []
  await loadAnnotationCases()
}

async function loadAnnotationCases() {
  importLoading.value = true
  try {
    const data = await listAnnotationCases({ anomaly_type: importAnomalyType.value.trim() || undefined, limit: 200 })
    annotationCases.value = (data.items || []).map((it, i) => ({ ...it, _key: `${it.window_id}-${i}` }))
  } catch (e) {
    annotationCases.value = []
    ElMessage.error(`获取标注案例失败：${e.message || e}（确认标注子系统在线且已有标注）`)
  } finally {
    importLoading.value = false
  }
}

function onImportSelect(rows) {
  selectedCases.value = rows
}

async function doImportCases() {
  if (!selectedCases.value.length) return ElMessage.warning('请先勾选要导入的案例')
  importSubmitting.value = true
  try {
    const cases = selectedCases.value.map((c) => ({
      anomaly_type: c.anomaly_type,
      note: c.note,
      fault_type_description: c.fault_type_description,
      log_text: c.log_text,
      window_id: c.window_id,
      package_name: c.package_name,
      external_run_id: c.external_run_id,
    }))
    const res = await importAnnotationCases({ cases })
    ElMessage.success(`导入完成：知识库 ${res.imported} 条、新建故障类型 ${res.created_fault_types} 个、知识图谱更新 ${res.kg_appended} 条`)
    if (res.errors && res.errors.length) {
      ElMessage.warning(`其中 ${res.errors.length} 条有问题（见控制台）`)
      console.warn('导入错误：', res.errors)
    }
    showImport.value = false
    loadLogs()
  } catch (e) {
    ElMessage.error(`导入失败：${e.message || e}`)
  } finally {
    importSubmitting.value = false
  }
}

const tagType = (tag) => ({ red: 'danger', yellow: 'warning', green: 'success' }[tag] || 'info')

const formatDate = (d) => {
  if (!d) return ''
  const s = String(d)
  const hasTz = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(s)
  return new Date(hasTz ? s : s + 'Z').toLocaleString('zh-CN')
}

async function loadTypes() {
  typeLoading.value = true
  faultTypes.value = await listFaultTypes().finally(() => (typeLoading.value = false))
}

async function loadLogs() {
  logLoading.value = true
  const res = await listLogs({ page: logPage.value, page_size: 20, fault_type_id: filterTypeId.value || undefined }).finally(() => (logLoading.value = false))
  logs.value = res.items
  logTotal.value = res.total
}

function openTypeDialog(row) {
  editingType.value = row
  typeForm.value = row ? { name: row.name, description: row.description || '', color_tag: row.color_tag } : { name: '', description: '', color_tag: 'red' }
  typeDialogVisible.value = true
}

async function submitType() {
  if (!typeForm.value.name) return ElMessage.warning('请填写类型名称')
  if (editingType.value) {
    await updateFaultType(editingType.value.id, typeForm.value)
  } else {
    await createFaultType(typeForm.value)
  }
  typeDialogVisible.value = false
  ElMessage.success('保存成功')
  loadTypes()
}

async function deleteType(id) {
  await deleteFaultType(id)
  ElMessage.success('已删除')
  loadTypes()
}

function onFileChange(file) {
  selectedFile.value = file.raw
}

async function doUpload() {
  if (!uploadTypeId.value) return ElMessage.warning('请选择故障类型')
  if (!selectedFile.value) return ElMessage.warning('请选择文件')
  uploading.value = true
  const fd = new FormData()
  fd.append('file', selectedFile.value)
  fd.append('fault_type_id', uploadTypeId.value)
  if (uploadSummary.value) fd.append('summary', uploadSummary.value)
  await uploadLog(fd).finally(() => (uploading.value = false))
  ElMessage.success('上传成功，已自动向量化入库')
  showUpload.value = false
  selectedFile.value = null
  uploadSummary.value = ''
  loadLogs()
}

async function deleteLogItem(id) {
  await deleteLog(id)
  ElMessage.success('已删除')
  loadLogs()
}

async function doBatchIndex() {
  const res = await batchIndex()
  ElMessage.success(`成功向量化 ${res.indexed} 条日志`)
  loadLogs()
}

async function doCreateCase() {
  if (!caseForm.value.fault_type_id) return ElMessage.warning('请选择故障类型')
  if (!caseForm.value.root_cause.trim()) return ElMessage.warning('请填写根因')
  caseSaving.value = true
  try {
    await createKnowledgeCase({
      fault_type_id: caseForm.value.fault_type_id,
      root_cause: caseForm.value.root_cause.trim(),
      solution: caseForm.value.solution.trim() || null,
      sample_log: caseForm.value.sample_log.trim() || null,
    })
    ElMessage.success('知识案例已保存并向量化入库')
    showCase.value = false
    caseForm.value = { fault_type_id: null, root_cause: '', solution: '', sample_log: '' }
    loadLogs()
  } catch (e) {
    ElMessage.error(`保存失败：${e.message || e}`)
  } finally {
    caseSaving.value = false
  }
}

onMounted(() => {
  loadTypes()
  loadLogs()
})
</script>

<style scoped>
.kb-page { display: flex; flex-direction: column; gap: 20px; }
.card-header { display: flex; align-items: center; justify-content: space-between; }
.header-actions { display: flex; gap: 8px; align-items: center; }
.pagination { margin-top: 12px; display: flex; justify-content: flex-end; }
</style>
