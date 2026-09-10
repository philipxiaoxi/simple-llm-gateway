#!/usr/bin/env node
/**
 * 构建后生成预压缩产物（.br / .gz）。
 *
 * 线上由 uvicorn 直接托管 dist，没有 Nginx 做压缩；在构建期压好，
 * 运行时按 Accept-Encoding 直接发送，既省 CPU 又能用压缩率更高的 brotli。
 *
 * 只处理体积大、文本类的产物；图片/字体本身已是压缩格式，跳过。
 */
import { brotliCompressSync, gzipSync, constants } from 'node:zlib'
import { readdirSync, readFileSync, statSync, writeFileSync, utimesSync } from 'node:fs'
import { join, extname } from 'node:path'

const DIST = new URL('../dist/', import.meta.url).pathname
const TARGET_DIRS = ['assets']
const TARGET_EXT = new Set(['.js', '.css', '.html', '.svg', '.json', '.webmanifest'])
const MIN_BYTES = 1024

function collect(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const info = statSync(full)
    if (info.isDirectory()) collect(full, out)
    else if (TARGET_EXT.has(extname(entry)) && info.size >= MIN_BYTES) out.push(full)
  }
  return out
}

let saved = 0
let count = 0
for (const dir of TARGET_DIRS) {
  for (const file of collect(join(DIST, dir))) {
    const source = readFileSync(file)
    const brotli = brotliCompressSync(source, {
      params: {
        [constants.BROTLI_PARAM_QUALITY]: 11,
        [constants.BROTLI_PARAM_SIZE_HINT]: source.length,
      },
    })
    const gzip = gzipSync(source, { level: 9 })
    writeFileSync(`${file}.br`, brotli)
    writeFileSync(`${file}.gz`, gzip)
    // 与源文件保持同一时间戳，便于比对构建产物是否为同一次
    const stamp = statSync(file).mtime
    utimesSync(`${file}.br`, stamp, stamp)
    utimesSync(`${file}.gz`, stamp, stamp)
    saved += source.length - brotli.length
    count += 1
    console.log(
      `  ${file.slice(DIST.length)}  ${(source.length / 1024).toFixed(1)} KB → ` +
        `br ${(brotli.length / 1024).toFixed(1)} KB / gz ${(gzip.length / 1024).toFixed(1)} KB`,
    )
  }
}
console.log(`预压缩完成：${count} 个文件，brotli 相比原始体积减少 ${(saved / 1024).toFixed(0)} KB`)
