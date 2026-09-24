import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const dataImportPath = new URL('../src/views/DataImport/index.vue', import.meta.url)

test('data import list and preview expose all training ingestion counters', async () => {
  const source = await readFile(dataImportPath, 'utf8')
  const counters = [
    'training_complete_count',
    'training_incomplete_count',
    'training_duplicate_count',
    'training_failed_count',
    'training_parse_failed_count',
  ]

  for (const counter of counters) {
    assert.ok(source.split(counter).length >= 3, `${counter} must appear in list and preview`)
  }
  assert.match(source, /partial_success/)
  assert.match(source, /部分成功/)
  assert.match(source, /ingest_status\s*===\s*['"]partial_success['"]/)
  assert.match(source, /\['success',\s*'partial_success',\s*'failed'\]\.includes\(row\.ingest_status\)/)
  assert.match(source, /row\.ingest_status\s*===\s*['"]failed['"][\s\S]*?row\.ingest_error/)
})

test('data import keeps existing upload, edit, download, reingest, and delete workflows', async () => {
  const source = await readFile(dataImportPath, 'utf8')

  for (const handler of [
    'handleUpload',
    'submitEdit',
    'handleDownload',
    'handleReingest',
    'handleDelete',
  ]) {
    assert.match(source, new RegExp(handler))
  }
})
