import { chromium } from 'playwright'

const BASE = 'http://127.0.0.1:5173'
const errors = []

/**
 * 界面自检：跑一遍真实交互，校验无 console 报错并输出截图。
 * 需要后端与 Vite 都已启动（make dev-backend / npm run dev）。
 */
// 本机没装 Playwright 自带 Chromium 时，退回系统 Edge/Chrome（不额外下载）
async function launch() {
  for (const channel of [process.env.PW_CHANNEL, 'msedge', 'chrome', undefined]) {
    try {
      return await chromium.launch({ channel, args: ['--no-proxy-server'] })
    } catch {
      /* 试下一个 */
    }
  }
  throw new Error('找不到可用浏览器：请 npx playwright install chromium，或安装 Edge/Chrome')
}

const browser = await launch()
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await context.newPage()

page.on('console', (message) => {
  if (message.type() === 'error') errors.push(`console: ${message.text()}`)
})
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`))

async function waitForAnswer() {
  await page.waitForFunction(
    () => {
      const bubbles = Array.from(document.querySelectorAll('.bubble .text'))
      const last = bubbles[bubbles.length - 1]
      return Boolean(last && (last.textContent ?? '').trim().length > 24)
    },
    { timeout: 45000 },
  )
  await page.waitForTimeout(1500)
}

// 先登录（agent01 既能对话也能进审核台）
await page.goto(`${BASE}/login`, { waitUntil: 'networkidle' })
await page.screenshot({ path: 'shots/00-login.png' })
await page.fill('input[autocomplete="username"]', 'agent01')
await page.fill('input[type="password"]', 'demo1234')
await page.click('button[type="submit"]')
await page.waitForSelector('textarea', { timeout: 20000 })
await page.waitForSelector('h2')
await page.screenshot({ path: 'shots/01-empty.png' })

// 1) 政策问答：检索 → 生成 → 引用校验
await page.fill('textarea', '退款一般多久到账？')
await page.keyboard.press('Enter')
await waitForAnswer()
await page.screenshot({ path: 'shots/02-rag.png' })

// 2) 高额退款 → 审批条
await page.fill('textarea', '订单 SO202610180001 我要退款，耳机有杂音')
await page.keyboard.press('Enter')
await page.waitForSelector('.banner', { timeout: 45000 })
await page.waitForTimeout(1200)
await page.screenshot({ path: 'shots/03-approval.png' })

// 3) 先看审核台里的这条待审（用 SPA 内跳转，保留会话状态）
await page.click('a:has-text("人工审核")')
await page.waitForSelector('text=人工审核工作台')
await page.waitForTimeout(1200)
await page.screenshot({ path: 'shots/04-review.png' })

// 4) 回到对话页直接审批，验证图恢复
await page.click('a:has-text("对话工作台")')
await page.waitForSelector('.banner', { timeout: 20000 })
await page.click('text=通过并执行退款')
await page.waitForFunction(() => document.body.innerText.includes('退款已提交'), { timeout: 45000 })
await page.waitForTimeout(1200)
const approved = (await page.content()).includes('退款已提交')
await page.screenshot({ path: 'shots/06-approved.png' })

// 5) 知识库检索调试台
await page.click('a:has-text("知识库")')
await page.waitForSelector('text=检索调试台')
await page.waitForTimeout(1800)
await page.screenshot({ path: 'shots/05-knowledge.png' })

console.log(`approve_inline=${approved}`)
console.log(errors.length ? errors.join('\n') : 'NO_CONSOLE_ERRORS')
await browser.close()
