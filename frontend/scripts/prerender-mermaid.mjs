/**
 * 构建期把 docs/项目讲解.md 里的 ```mermaid 块预渲染成 SVG，
 * 输出 docs/.mermaid.json（{ "0": "<svg…>", … }）。
 * 这样生成的 HTML 不依赖 CDN，离线/打印都能看到图。
 *
 * 用法：cd frontend && node scripts/prerender-mermaid.mjs
 */
import { readFile, writeFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'
import { resolve } from 'node:path'

import { chromium } from 'playwright'

const ROOT = resolve(process.cwd(), '..')
const DOC = resolve(ROOT, 'docs/项目讲解.md')
const OUT = resolve(ROOT, 'docs/.mermaid.json')
const MERMAID = resolve(process.cwd(), 'node_modules/mermaid/dist/mermaid.js')

const markdown = (await readFile(DOC, 'utf8')).replace(/\r\n/g, '\n')
const blocks = [...markdown.matchAll(/```mermaid\n([\s\S]*?)```/g)].map((m) => m[1].trim())

if (blocks.length === 0) {
  console.log('没有找到 mermaid 代码块')
  process.exit(0)
}

async function launchBrowser() {
  // 优先 Playwright 自带 Chromium；没装就退回系统 Edge/Chrome
  for (const channel of [process.env.PW_CHANNEL, 'msedge', 'chrome', undefined]) {
    try {
      return await chromium.launch({ channel, args: ['--no-proxy-server'] })
    } catch {
      /* 试下一个 */
    }
  }
  throw new Error('找不到可用浏览器')
}

const browser = await launchBrowser()
const page = await browser.newPage()
await page.setContent('<html><body></body></html>')
await page.addScriptTag({ path: MERMAID })

const svgs = await page.evaluate(async (sources) => {
  window.mermaid.initialize({
    startOnLoad: false,
    theme: 'neutral',
    securityLevel: 'loose',
    flowchart: { nodeSpacing: 26, rankSpacing: 34, padding: 8 },
    sequence: { actorMargin: 22, width: 118, boxMargin: 8, messageMargin: 28, mirrorActors: false },
  })
  const result = []
  for (let index = 0; index < sources.length; index += 1) {
    // eslint-disable-next-line no-await-in-loop
    const { svg } = await window.mermaid.render(`diagram-${index}`, sources[index])
    result.push(svg)
  }
  return result
}, blocks)

const payload = Object.fromEntries(svgs.map((svg, index) => [String(index), svg]))
await writeFile(OUT, JSON.stringify(payload), 'utf8')

console.log(`已预渲染 ${svgs.length} 张图 → docs/.mermaid.json`)
await browser.close()
