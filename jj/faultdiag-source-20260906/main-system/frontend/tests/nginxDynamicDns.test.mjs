import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const dockerfilePath = new URL('../Dockerfile', import.meta.url)
const entrypointPath = new URL('../docker-entrypoint.sh', import.meta.url)
const nginxTemplatePath = new URL('../nginx/default.conf', import.meta.url)

async function source(path) {
  return readFile(path, 'utf8')
}

function locationBlock(config, path) {
  const marker = `location ${path} {`
  const start = config.indexOf(marker)
  assert.notEqual(start, -1, `missing ${marker}`)

  let depth = 0
  for (let index = start + marker.length - 1; index < config.length; index += 1) {
    if (config[index] === '{') depth += 1
    if (config[index] === '}') depth -= 1
    if (depth === 0) return config.slice(start, index + 1)
  }

  assert.fail(`unterminated ${marker}`)
}

function assertNoApiCors(block) {
  assert.doesNotMatch(
    block,
    /Access-Control-Allow-(?:Origin|Credentials|Headers|Methods)|Access-Control-Expose-Headers|Authorization|X-Token/i,
  )
}

// ─── root nginx template (Case A) ─────────────────────────────

test('root nginx template uses fixed upstreams without resolver and without DNS injection', async () => {
  const config = await source(nginxTemplatePath)

  // 不再有 resolver / set 变量
  assert.doesNotMatch(config, /\bresolver\s/)
  assert.doesNotMatch(config, /\bset\s+\$main_backend\b/)
  assert.doesNotMatch(config, /\bset\s+\$annotation_frontend\b/)
  assert.doesNotMatch(config, /\bset\s+\$annotation_backend\b/)

  // 不再有 `${NGINX_DNS_RESOLVER}` / `${BACKEND_DNS_HOST}` / `${BACKEND_PORT}` 占位符
  assert.doesNotMatch(config, /\$\{NGINX_DNS_RESOLVER\}/)
  assert.doesNotMatch(config, /\$\{BACKEND_DNS_HOST\}/)
  assert.doesNotMatch(config, /\$\{BACKEND_PORT\}/)

  const api = locationBlock(config, '/api/')
  assert.match(api, /proxy_pass http:\/\/backend:8000;/)
  assert.doesNotMatch(api, /rewrite\s/)

  const annotationFrontend = locationBlock(config, '/annotate/')
  assert.match(annotationFrontend, /proxy_pass http:\/\/frontend-annotate:80;/)
  assert.doesNotMatch(annotationFrontend, /rewrite\s/)

  // /annotate-api/ 用 nginx 前缀替换（proxy_pass trailing /api/，无 rewrite）
  const annotationApi = locationBlock(config, '/annotate-api/')
  assert.match(annotationApi, /proxy_pass http:\/\/backend-annotate:8000\/api\//)
  assert.doesNotMatch(annotationApi, /rewrite\s/)
})

test('root nginx template enables credential-free CORS only for static responses', async () => {
  const config = await source(nginxTemplatePath)
  const staticFiles = locationBlock(config, '/')
  const api = locationBlock(config, '/api/')
  const annotationApi = locationBlock(config, '/annotate-api/')

  assert.match(staticFiles, /add_header Access-Control-Allow-Origin "\*" always;/)
  assertNoApiCors(api)
  assertNoApiCors(annotationApi)
})

// ─── entrypoint (Case A: no-envsubst, Case B: fixed upstream) ─

test('entrypoint uses fixed upstreams: Case A cp template, Case B static backend:8000, no resolver', async () => {
  const entrypoint = await source(entrypointPath)

  assert.match(entrypoint, /^set -eu$/m)

  // Case A: 直接用 cp，不再有 envsubst / resolver / 三个 set 变量
  assert.match(entrypoint, /cp \/etc\/nginx\/templates\/default\.conf\.template \/etc\/nginx\/conf\.d\/default\.conf/)

  // 不再有 NGINX_DNS_RESOLVER / qualify_dns_host / 死代码
  assert.doesNotMatch(entrypoint, /\bNGINX_DNS_RESOLVER\b/)
  assert.doesNotMatch(entrypoint, /\bdiscover_kubernetes_search_suffix\b/)
  assert.doesNotMatch(entrypoint, /\bqualify_dns_host\b/)
  assert.doesNotMatch(entrypoint, /\bBACKEND_DNS_HOST\b/)
  assert.doesNotMatch(entrypoint, /\bANNOTATION_FRONTEND_DNS_HOST\b/)
  assert.doesNotMatch(entrypoint, /\bANNOTATION_BACKEND_DNS_HOST\b/)
  assert.doesNotMatch(entrypoint, /\benvsubst\b/)

  // Case B: 固定 upstream (backend:8000)，不再有 resolver / set
  assert.doesNotMatch(entrypoint, /resolver\s+\$\{NGINX_DNS_RESOLVER\}/)
  assert.doesNotMatch(entrypoint, /set \\\$main_backend/)
  assert.match(entrypoint, /proxy_pass http:\/\/backend:8000;\s*$/m)

  assert.match(entrypoint, /exec nginx -g ['"]daemon off;['"]/)
})

// ─── Dockerfile ───────────────────────────────────────────────

test('Docker image installs the root config as a template and runs the custom entrypoint', async () => {
  const dockerfile = await source(dockerfilePath)

  assert.match(dockerfile, /COPY nginx\/default\.conf \/etc\/nginx\/templates\/default\.conf\.template/)
  assert.match(dockerfile, /COPY docker-entrypoint\.sh \/docker-entrypoint\.sh/)
  assert.match(dockerfile, /RUN chmod \+x \/docker-entrypoint\.sh/)
  assert.match(dockerfile, /ENTRYPOINT \["\/docker-entrypoint\.sh"\]/)
  assert.doesNotMatch(dockerfile, /COPY nginx\/default\.conf \/etc\/nginx\/conf\.d\/default\.conf/)
})