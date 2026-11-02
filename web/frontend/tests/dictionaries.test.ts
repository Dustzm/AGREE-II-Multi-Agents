import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'
import en from '../src/locales/en-US.json'
import zh from '../src/locales/zh-CN.json'

function flatten(value: Record<string, unknown>, prefix = '', output: Record<string, string> = {}) {
    for (const [key, child] of Object.entries(value)) {
        const name = prefix ? `${prefix}.${key}` : key
        if (typeof child === 'string') output[name] = child
        else if (child && typeof child === 'object') flatten(child as Record<string, unknown>, name, output)
    }
    return output
}

function sourceFiles(directory: string): string[] {
    return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
        const path = resolve(directory, entry.name)
        return entry.isDirectory() ? sourceFiles(path) : /\.tsx?$/.test(entry.name) ? [path] : []
    })
}

// Brand names, technical identifiers, units and language-switch labels keep their spelling.
const allowedText = new Set(['AMA', 'AGREE-II Multi-Agents', 'D', 'ID:', '.md', 'ms', 'EN'])
const allowedAttributes = new Set(['AMA', '1-7'])

describe('bilingual resource coverage', () => {
    const english = flatten(en)
    const chinese = flatten(zh)

    it('has matching keys and interpolation parameters', () => {
        expect(Object.keys(chinese).sort()).toEqual(Object.keys(english).sort())
        const parameters = (value: string) => [...value.matchAll(/\{\{([^}]+)\}\}/g)].map(match => match[1]).sort()
        for (const [key, value] of Object.entries(english)) {
            expect(parameters(chinese[key]), key).toEqual(parameters(value))
        }
    })

    it('has resources for static translation keys and client descriptors', () => {
        const missing: string[] = []
        for (const path of sourceFiles(resolve('src'))) {
            const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true)
            function visit(node: ts.Node) {
                if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && ['t', 'notifySuccess'].includes(node.expression.text)) {
                    const key = node.arguments[0]
                    if (key && ts.isStringLiteral(key) && !(key.text.replace('server:', 'server.') in english)) missing.push(`${path}: ${key.text}`)
                }
                if (ts.isPropertyAssignment(node) && node.name.getText(source) === 'key' && ts.isStringLiteral(node.initializer)) {
                    const key = node.initializer.text
                    // Ignore unrelated domain/configuration keys; translation descriptors contain a dotted resource path.
                    if (key.includes('.') && !(key in english) && !(`server.${key}` in english)) missing.push(`${path}: ${key}`)
                }
                ts.forEachChild(node, visit)
            }
            visit(source)
        }
        expect(missing).toEqual([])
    })

    it('rejects hardcoded visible JSX text and auxiliary attributes', () => {
        const hardcoded: string[] = []
        for (const path of sourceFiles(resolve('src'))) {
            const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true)
            function visit(node: ts.Node) {
                if (ts.isJsxText(node)) {
                    const text = node.text.trim()
                    if (/[A-Za-z\u4e00-\u9fff]/.test(text) && !allowedText.has(text)) hardcoded.push(`${path}: ${text}`)
                }
                if (ts.isJsxAttribute(node) && ['title', 'placeholder', 'aria-label', 'alt'].includes(node.name.getText(source))) {
                    const value = node.initializer
                    if (value && ts.isStringLiteral(value) && !allowedAttributes.has(value.text)) hardcoded.push(`${path}: ${value.text}`)
                }
                ts.forEachChild(node, visit)
            }
            visit(source)
        }
        expect(hardcoded).toEqual([])
    })
})
