#!/usr/bin/env node
/**
 * patch-dist.js - Agent-TARS 运行时增强补丁
 * 
 * 功能:
 * 
 * 1. DOM 工具延时（browser_press_key, browser_click）
 *    - 使用 keyDown/keyUp 替代 press，支持配置按键持续时间
 *    - 点击操作使用 mouseDown/mouseUp 替代 click
 *    - 环境变量: AGENT_PRESS_DURATION_MS（默认 30ms）
 * 
 * 2. GUI 视觉工具等待时间优化
 *    - handleClick/handleDoubleClick/handleRightClick 后的等待时间可配置
 *    - browser_vision_control 内部 sleep 可配置
 *    - browser_navigate 页面加载后等待时间可配置
 *    - 环境变量: AGENT_CLICK_WAIT_MS, AGENT_NAVIGATE_WAIT_MS
 * 
 * 3. Agent Loop 暂停/恢复（模型思考时冻结网页）
 *    - Agent Loop Start: 创建 CDP client，注入定时器拦截脚本
 *    - 工具执行前: 恢复网页动画和定时器
 *    - 工具执行后: 等待 n ms 后暂停网页（截图时页面静止）
 *    - 环境变量: AGENT_PAUSE_ENABLED, AGENT_PAUSE_WAIT_MS（默认 500ms）
 * 
 * 4. Select 元素标记
 *    - 自动标记页面上的所有 <select> 元素
 *    - 在元素上方显示橙色角标（包含选择器和可选值）
 *    - 支持 browser_select 工具的文本匹配
 * 
 * 5. 日期/时间输入框标记
 *    - 自动标记 input[type="date/time/datetime-local/month/week"]
 *    - 在元素上方显示紫色角标（包含选择器和格式提示）
 * 
 * 6. 浏览器对话框自动处理
 *    - 监听 alert/confirm/prompt/beforeunload 对话框
 *    - 自动处理（默认 accept）
 *    - 在页面右下角显示红色角标提示 Agent
 *    - 下次工具调用前自动移除角标
 *    - 环境变量: AGENT_DIALOG_CONFIRM_DEFAULT, AGENT_DIALOG_PROMPT_DEFAULT
 * 
 * 7. Hotkey 按键映射修复
 *    - 修复单字母按键映射错误（a -> KeyA）
 *    - 修复 macOS CDP commands 参数被忽略的问题
 * 
 * 8. 录屏功能（screencast-recorder-cdp.js）
 *    - 工具执行时 CDP 被动推帧（不阻塞交互）
 *    - 暂停等待期间主动截图（确保静态页面也能捕获）
 *    - 模型思考时暂停录制
 *    - 最终合成流畅的交互视频（无思考延时）
 *    - 视频输出: ./recordings/agent_<sessionId>_<timestamp>.mp4
 *    - 环境变量: AGENT_RECORD_ENABLED, AGENT_RECORD_FPS, AGENT_RECORD_QUALITY, AGENT_RECORD_OUTPUT
 * 
 * 使用方法: 
 *   1. 修改 TypeScript 源码后，先编译: pnpm run build
 *   2. 然后运行此脚本: node patch-dist.js
 *   3. 此脚本会修改编译后的 dist 文件
 * 
 * 运行时环境变量:
 *   # 暂停/恢复功能
 *   AGENT_PAUSE_ENABLED=true/false    开关暂停/恢复（默认 true）
 *   AGENT_PAUSE_WAIT_MS=500           工具执行后等待多久再暂停（默认 500ms）
 *   
 *   # DOM 工具延时
 *   AGENT_PRESS_DURATION_MS=30        按键/点击持续时间（默认 30ms）
 *   
 *   # GUI 视觉工具等待
 *   AGENT_CLICK_WAIT_MS=0             点击后等待时间（默认 0，原始为 800ms）
 *   AGENT_NAVIGATE_WAIT_MS=0          页面导航后等待时间（默认 0）
 *   
 *   # 对话框处理
 *   AGENT_DIALOG_CONFIRM_DEFAULT=true confirm 对话框默认行为
 *   AGENT_DIALOG_PROMPT_DEFAULT=""    prompt 对话框默认输入值
 *   
 *   # 录屏功能
 *   AGENT_RECORD_ENABLED=true/false   开启录屏功能（默认 false）
 *   AGENT_RECORD_FPS=10               录制帧率（默认 10fps）
 *   AGENT_RECORD_QUALITY=80           JPEG 质量 1-100（默认 80）
 *   AGENT_RECORD_OUTPUT=./recordings  视频输出目录
 *   AGENT_RECORD_MODULE_PATH=...      录屏模块路径（默认同目录 screencast-recorder-cdp.js）
 *   AGENT_RECORD_STRATEGY=cdp_legacy  录屏策略: cdp_legacy(CDP+Legacy混合) 或 cdp_only(全CDP)
 * 
 * 注意: 每次重新编译都需要重新运行此脚本！
 */

const fs = require('fs');
const path = require('path');

// ============ 可配置参数 ============
const PRESS_DURATION = 30;
const TOOL_WAIT_TIME = 500;
const SCREENCAST_FPS = 10;  // 录屏帧率
const SCREENCAST_QUALITY = 80;  // JPEG 质量

// ============ 录屏模块路径（注入代码中使用） ============
// 使用 CDP 非阻塞版本（工具执行时被动推帧 + 等待期主动截图）
const SCREENCAST_RECORDER_PATH = path.join(__dirname, 'screencast-recorder-cdp.js');

console.log('======================================');
console.log('[CUSTOM PATCH] 精简版 - DOM工具延时 + 暂停/恢复 + Hotkey修复 + 录屏');
console.log('======================================\n');

// 检查录屏模块是否存在
if (fs.existsSync(SCREENCAST_RECORDER_PATH)) {
    console.log(`[录屏模块] ✅ 已找到: screencast-recorder-cdp.js`);
    console.log(`[录屏模块]    模式: CDP 非阻塞（工具执行被动推帧 + 等待期主动截图）`);
} else {
    console.log(`[录屏模块] ⚠️ 未找到: ${SCREENCAST_RECORDER_PATH}`);
    console.log('          录屏功能将不可用');
}
console.log('');

// ============================================
// 修复 Hotkey bug: 单字母映射错误导致 ctrl+a 输入字符 'a'
// ============================================
console.log('0. 修复 Hotkey 按键映射...');

const hotkeyKeyMapPaths = [];
try {
    const nodeModulesBase = path.join(__dirname, 'node_modules/.pnpm');
    if (fs.existsSync(nodeModulesBase)) {
        const dirs = fs.readdirSync(nodeModulesBase).filter(d => d.startsWith('@agent-infra+puppeteer-enhance@'));
        for (const dir of dirs) {
            const keyMapPath = path.join(nodeModulesBase, dir, 'node_modules/@agent-infra/puppeteer-enhance/dist/hotkey/key-map.js');
            if (fs.existsSync(keyMapPath)) {
                hotkeyKeyMapPaths.push(keyMapPath);
            }
        }
    }
} catch (e) {
    console.log('   搜索 puppeteer-enhance 时出错:', e.message);
}

for (const keyMapPath of hotkeyKeyMapPaths) {
    console.log(`   处理: ...${keyMapPath.substring(keyMapPath.length - 60)}`);

    let keyMapContent = fs.readFileSync(keyMapPath, 'utf-8');

    // 检测是否已修复 - 必须精确匹配独立的 "a: 'KeyA'" 而不是 "keya: 'KeyA'" 的子串
    const hasHotkeyFixMarker = keyMapContent.includes('[HOTKEY-FIX]');
    // 使用正则确保 'a' 前面不是字母（排除 keya: 'KeyA' 的误判）
    const hasFixedSingleA = /[^a-z]a:\s*['"]KeyA['"]/.test(keyMapContent);

    if (hasHotkeyFixMarker || hasFixedSingleA) {
        console.log('   ⚠️ Hotkey 已修复，跳过');
        console.log(`      (marker=${hasHotkeyFixMarker}, fixedA=${hasFixedSingleA})`);
    } else {
        // 备份
        const backupPath = keyMapPath + '.backup';
        if (!fs.existsSync(backupPath)) {
            fs.copyFileSync(keyMapPath, backupPath);
            console.log('   备份到: ' + backupPath.substring(backupPath.length - 40));
        }

        // 修复: 将单字母 'A'-'Z' 映射改为键码 'KeyA'-'KeyZ'
        // 原始映射: a: 'A', b: 'B', ... z: 'Z'
        // 修复后:   a: 'KeyA', b: 'KeyB', ... z: 'KeyZ'
        const letters = 'abcdefghijklmnopqrstuvwxyz'.split('');
        let fixCount = 0;

        for (const letter of letters) {
            const upper = letter.toUpperCase();
            // 匹配 "a: 'A'" 或 "a: \"A\"" 格式
            const pattern1 = new RegExp(`([^k])${letter}: '${upper}'`, 'g');
            const pattern2 = new RegExp(`([^k])${letter}: "${upper}"`, 'g');

            if (pattern1.test(keyMapContent)) {
                keyMapContent = keyMapContent.replace(pattern1, `$1${letter}: 'Key${upper}'`);
                fixCount++;
            } else if (pattern2.test(keyMapContent)) {
                keyMapContent = keyMapContent.replace(pattern2, `$1${letter}: "Key${upper}"`);
                fixCount++;
            }
        }

        // 添加标记
        keyMapContent = keyMapContent.replace(
            '"use strict";',
            '"use strict";\n// [HOTKEY-FIX] Single letter keys mapped to KeyX format'
        );

        fs.writeFileSync(keyMapPath, keyMapContent);
        console.log(`   ✅ 修复了 ${fixCount} 个字母按键映射`);
    }
}

console.log('');

// ============================================
// 0.5 修复 macOS Hotkey CDP bug: page.keyboard.down 不支持 commands 参数
// ============================================
console.log('0.5 修复 macOS Hotkey CDP (commands 参数)...');

const hotkeyIndexPaths = [];
try {
    const nodeModulesBase = path.join(__dirname, 'node_modules/.pnpm');
    if (fs.existsSync(nodeModulesBase)) {
        const dirs = fs.readdirSync(nodeModulesBase).filter(d => d.startsWith('@agent-infra+puppeteer-enhance@'));
        for (const dir of dirs) {
            const indexPath = path.join(nodeModulesBase, dir, 'node_modules/@agent-infra/puppeteer-enhance/dist/hotkey/index.js');
            if (fs.existsSync(indexPath)) {
                hotkeyIndexPaths.push(indexPath);
            }
        }
    }
} catch (e) {
    console.log('   搜索 hotkey/index.js 时出错:', e.message);
}

for (const indexPath of hotkeyIndexPaths) {
    console.log(`   处理: ...${indexPath.substring(indexPath.length - 60)}`);

    let indexContent = fs.readFileSync(indexPath, 'utf-8');

    // 检测是否已修复
    if (indexContent.includes('[HOTKEY-CDP-FIX]')) {
        console.log('   ⚠️ Hotkey CDP 已修复，跳过');
        continue;
    }

    // 备份
    const backupPath = indexPath + '.backup';
    if (!fs.existsSync(backupPath)) {
        fs.copyFileSync(indexPath, backupPath);
        console.log('   备份到: ' + backupPath.substring(backupPath.length - 40));
    }

    // 修复 macOSCDPHotKey 方法
    // 原始代码:
    //   await page.keyboard.down(command.key, { commands: [command.commands] });
    //   await external_delay_default()(options.delay);
    //   await page.keyboard.up(command.key);
    // 
    // 问题: Puppeteer 的 keyboard.down() 不支持 commands 参数，会被忽略
    // 修复: 使用 CDP session 直接发送 Input.dispatchKeyEvent

    const macOSPattern = /await page\.keyboard\.down\(command\.key,\s*\{\s*commands:\s*\[\s*command\.commands\s*\]\s*\}\s*\);\s*\n\s*await external_delay_default\(\)\(options\.delay\);\s*\n\s*await page\.keyboard\.up\(command\.key\);/g;

    if (macOSPattern.test(indexContent)) {
        indexContent = indexContent.replace(
            macOSPattern,
            `// [HOTKEY-CDP-FIX] Use CDP session for macOS system commands (Puppeteer ignores commands param)
            const _cdpClient = await page.target().createCDPSession();
            const _keyChar = command.key.replace('Key', '').toLowerCase();
            const _cmdName = command.commands.charAt(0).toLowerCase() + command.commands.slice(1);
            console.log('[HOTKEY-CDP-FIX] Sending CDP command:', _cmdName, 'for key:', command.key);
            await _cdpClient.send('Input.dispatchKeyEvent', {
                type: 'keyDown',
                code: command.key,
                key: _keyChar,
                commands: [_cmdName]
            });
            await external_delay_default()(options.delay);
            await _cdpClient.send('Input.dispatchKeyEvent', {
                type: 'keyUp',
                code: command.key,
                key: _keyChar
            });`
        );

        fs.writeFileSync(indexPath, indexContent);
        console.log('   ✅ macOS Hotkey CDP 修复成功');
    } else {
        console.log('   ⚠️ 未找到匹配模式，可能代码结构已变化');
    }
}

console.log('');

// 查找 @agent-tars/core dist 文件
const possiblePaths = [];

// 1. 本地构建版本
const localDist = path.join(__dirname, 'agent-tars/core/dist/index.js');
if (fs.existsSync(localDist)) {
    possiblePaths.push(localDist);
}

// 2. node_modules 中的 npm 包版本
try {
    const nodeModulesBase = path.join(__dirname, 'node_modules/.pnpm');
    if (fs.existsSync(nodeModulesBase)) {
        const dirs = fs.readdirSync(nodeModulesBase).filter(d => d.startsWith('@agent-tars+core@'));
        for (const dir of dirs) {
            const npmDist = path.join(nodeModulesBase, dir, 'node_modules/@agent-tars/core/dist/index.js');
            if (fs.existsSync(npmDist)) {
                possiblePaths.push(npmDist);
            }
        }
    }
} catch (e) {
    console.log('搜索 node_modules 时出错:', e.message);
}

// 3. 直接的 node_modules/@agent-tars/core
const directNodeModules = path.join(__dirname, 'node_modules/@agent-tars/core/dist/index.js');
if (fs.existsSync(directNodeModules) && !possiblePaths.includes(directNodeModules)) {
    possiblePaths.push(directNodeModules);
}

console.log(`找到 ${possiblePaths.length} 个 @agent-tars/core dist 文件:`);
possiblePaths.forEach((p, i) => {
    const isLocal = p.includes('agent-tars/core/dist') && !p.includes('node_modules');
    console.log(`  ${i + 1}. ${isLocal ? '[本地构建]' : '[npm包]'} ${p.substring(__dirname.length)}`);
});
console.log('');

if (possiblePaths.length === 0) {
    console.error('错误: 没有找到 @agent-tars/core dist 文件');
    console.error('请先运行: pnpm install && pnpm run bootstrap');
    process.exit(1);
}

let totalModified = 0;

for (const DIST_FILE of possiblePaths) {
    console.log(`\n正在处理: ${DIST_FILE.substring(__dirname.length)}`);
    console.log('-'.repeat(60));

    let content = fs.readFileSync(DIST_FILE, 'utf-8');

    // 检测是否已修改
    const hasPressKeyPatch = content.includes('[CUSTOM] browser_press_key:') || content.includes('[CUSTOM] browser_press_key: duration from args');
    const hasKeyEnumPatchCheck = content.includes('[CUSTOM-KEY-ENUM]') || content.includes('[CUSTOM-KEY-DURATION]');
    const hasPausePatch = content.includes('[CUSTOM-PATCH] pausePage injection');
    const hasSelectDialogPromptEarly = content.includes('<agent_injected_badges>');
    const hasBrowserSelectPatch = content.includes('[CUSTOM-SELECT-PATCH]');
    const hasDateInputPatch = content.includes('<date_input_handling>');
    const hasDateBadgeCode = content.includes('input[type="date"]');

    // 只有当所有修改都已完成时才跳过
    if (hasPressKeyPatch && hasKeyEnumPatchCheck && hasPausePatch && hasSelectDialogPromptEarly && hasBrowserSelectPatch && hasDateInputPatch && hasDateBadgeCode) {
        console.log('   ⚠️ 所有修改已完成，跳过');
        totalModified++;
        continue;
    }

    console.log(`   状态: 延时=${hasPressKeyPatch ? '✅' : '❌'}, 按键枚举=${hasKeyEnumPatchCheck ? '✅' : '❌'}, 暂停=${hasPausePatch ? '✅' : '❌'}, 日期=${hasDateInputPatch && hasDateBadgeCode ? '✅' : '❌'}`);

    // 备份
    const backupFile = DIST_FILE + '.backup';
    if (!fs.existsSync(backupFile)) {
        fs.copyFileSync(DIST_FILE, backupFile);
        console.log(`   备份到: ...${backupFile.substring(backupFile.length - 40)}`);
    }

    let modifiedCount = 0;

    // ============================================
    // 1. 修改 browser_press_key - DOM 工具延时
    // ============================================
    console.log('   1. 修改 browser_press_key (DOM工具延时)...');

    const pressPattern = /browser_press_key:\s*async\s*\(args\)\s*=>\s*\{\s*try\s*\{\s*await\s+page\.keyboard\.press\(args\.key\);/g;
    const pressPatternCustom = /\/\/ \[CUSTOM\] browser_press_key: Duration-based key press\s*\n\s*const pressDuration = parseInt\(process\.env\.AGENT_PRESS_DURATION_MS\) \|\| \d+;\s*\n\s*console\.log\(`\[CUSTOM\] browser_press_key: \$\{args\.key\} with duration=\$\{pressDuration\}ms`\);\s*\n\s*await page\.keyboard\.down\(args\.key\);\s*\n\s*await new Promise\(r => setTimeout\(r, pressDuration\)\);\s*\n\s*await page\.keyboard\.up\(args\.key\);/g;
    const hasPressDurationArgPatch = content.includes('[CUSTOM] browser_press_key: duration from args');
    if (!hasPressDurationArgPatch) {
        // Try to patch the already-patched version first (custom pattern)
        if (pressPatternCustom.test(content)) {
            content = content.replace(
                pressPatternCustom,
                `// [CUSTOM] browser_press_key: duration from args + key normalization
                            const _keyMap = {space:'Space',enter:'Enter',tab:'Tab',escape:'Escape',backspace:'Backspace',delete:'Delete',insert:'Insert',arrowleft:'ArrowLeft',arrowright:'ArrowRight',arrowup:'ArrowUp',arrowdown:'ArrowDown',pageup:'PageUp',pagedown:'PageDown',home:'Home',end:'End',capslock:'CapsLock'};
                            const _key = _keyMap[args.key.toLowerCase()] || args.key;
                            const defaultDuration = parseInt(process.env.AGENT_PRESS_DURATION_MS) || ${PRESS_DURATION};
                            const pressDuration = (args.duration_ms && args.duration_ms > 0) ? args.duration_ms : defaultDuration;
                            console.log(\`[CUSTOM] browser_press_key: \${_key} with duration=\${pressDuration}ms\`);
                            await page.keyboard.down(_key);
                            await new Promise(r => setTimeout(r, pressDuration));
                            await page.keyboard.up(_key);`
            );
            modifiedCount++;
            console.log('      ✅ browser_press_key 修改成功 (upgraded with duration_ms arg)');
        } else if (pressPattern.test(content)) {
            // Patch the original unpatched version
            content = content.replace(
                pressPattern,
                `browser_press_key: async (args)=>{
                        try {
                            // [CUSTOM] browser_press_key: duration from args + key normalization
                            const _keyMap = {space:'Space',enter:'Enter',tab:'Tab',escape:'Escape',backspace:'Backspace',delete:'Delete',insert:'Insert',arrowleft:'ArrowLeft',arrowright:'ArrowRight',arrowup:'ArrowUp',arrowdown:'ArrowDown',pageup:'PageUp',pagedown:'PageDown',home:'Home',end:'End',capslock:'CapsLock'};
                            const _key = _keyMap[args.key.toLowerCase()] || args.key;
                            const defaultDuration = parseInt(process.env.AGENT_PRESS_DURATION_MS) || ${PRESS_DURATION};
                            const pressDuration = (args.duration_ms && args.duration_ms > 0) ? args.duration_ms : defaultDuration;
                            console.log(\`[CUSTOM] browser_press_key: \${_key} with duration=\${pressDuration}ms\`);
                            await page.keyboard.down(_key);
                            await new Promise(r => setTimeout(r, pressDuration));
                            await page.keyboard.up(_key);`
            );
            modifiedCount++;
            console.log('      ✅ browser_press_key 修改成功');
        } else {
            console.log('      ❌ 未找到匹配模式');
        }
    } else {
        console.log('      ⚠️ 已修改过');
    }

    // 修改返回消息 (use _key for normalized key name, pressDuration for actual duration)
    const oldMsg = /text:\s*`Pressed key: \$\{args\.key\}`/g;
    if (oldMsg.test(content) && !content.includes('[CUSTOM] Pressed key:')) {
        content = content.replace(oldMsg, 'text: `[CUSTOM] Pressed key: ${_key} with duration=${pressDuration}ms`');
    }

    // ============================================
    // 1.1 修改 browser_press_key - 解除按键枚举限制
    // ============================================
    console.log('   1.1 修改 browser_press_key (解除按键枚举限制)...');

    const hasKeyEnumPatch = content.includes('[CUSTOM-KEY-ENUM]');
    // Match the full inputSchema object to replace it with key + duration_ms
    const hasKeyDurationPatch = content.includes('[CUSTOM-KEY-DURATION]');
    if (!hasKeyDurationPatch) {
        // Match browser_press_key's inputSchema specifically (anchor on the tool name + description)
        const keySchemaPattern = /description:\s*'Press a key on the keyboard',\s*inputSchema:\s*lib\.z\.object\(\{[\s\S]*?key:\s*(?:lib\.z\["enum"\]\(keyInputValues\)|lib\.z\.string\(\))\.describe\([^)]+\)[\s\S]*?\}\)/;
        content = content.replace(
            keySchemaPattern,
            `description: 'Press a key on the keyboard',
                    inputSchema: lib.z.object({
                        key: lib.z.string().describe(\`[CUSTOM-KEY-ENUM] [CUSTOM-KEY-DURATION] Name of the key to press. Supports: Space, Enter, Tab, Escape, Backspace, Delete, ArrowLeft, ArrowRight, ArrowUp, ArrowDown, F1-F12, Home, End, PageUp, PageDown, and any single character (a-z, 0-9, etc.)\`),
                        duration_ms: lib.z.number().optional().describe(\`How long to hold the key in milliseconds. Default: 30ms. For games that need held keys (e.g. movement), use 300-1000ms.\`)
                    })`
        );
        modifiedCount++;
        console.log('      ✅ browser_press_key 枚举限制已移除 + duration_ms 参数已添加');
    } else if (hasKeyDurationPatch) {
        console.log('      ⚠️ 已修改过');
    } else {
        console.log('      ❌ 未找到匹配模式');
    }

    // ============================================
    // 1.2 inject thought/step into ALL browser_* tool schemas
    // ============================================
    console.log('   1.2 注入 thought/step 到所有 browser_* 工具...');

    const THOUGHT_STEP_FIELDS = `
                        thought: lib.z.string().describe('Your observation of what happened after the last action and what you expect this action to do'),
                        step: lib.z.string().describe('Summarize the action you are taking and why'),`;
    const hasThoughtStepPatch = content.includes('[CUSTOM-THOUGHT-STEP]');
    if (!hasThoughtStepPatch) {
        // Tools that have inputSchema: lib.z.object({ ... }) and need thought/step injected
        const toolsWithSchema = [
            'browser_click', 'browser_form_input_fill', 'browser_select',
            'browser_hover', 'browser_evaluate', 'browser_scroll',
            'browser_press_key', 'browser_new_tab', 'browser_switch_tab',
            'browser_close_tab', 'browser_read_links', 'browser_tab_list',
            'browser_navigate',
        ];
        let injectedCount = 0;
        for (const toolName of toolsWithSchema) {
            // Match: name: 'toolName', ... inputSchema: lib.z.object({
            // and inject thought/step after the opening {
            const pattern = new RegExp(
                `(name:\\s*'${toolName}'[\\s\\S]*?inputSchema:\\s*lib\\.z\\.object\\(\\{)`,
            );
            if (pattern.test(content) && !content.includes(`name: '${toolName}'`) ? false :
                // Check this specific tool doesn't already have thought
                !content.substring(
                    content.indexOf(`name: '${toolName}'`),
                    content.indexOf(`name: '${toolName}'`) + 800
                ).includes('thought:')) {
                content = content.replace(pattern, `$1${THOUGHT_STEP_FIELDS}`);
                injectedCount++;
            }
        }
        if (injectedCount > 0) {
            // Add marker so we can detect this patch (in description, not name)
            content = content.replace(
                "description: 'Click an element on the page",
                "description: '[CUSTOM-THOUGHT-STEP] Click an element on the page"
            );
            modifiedCount++;
            console.log(`      ✅ thought/step 注入成功 (${injectedCount} 个工具)`);
        } else {
            console.log('      ❌ 未找到匹配工具');
        }
    } else {
        console.log('      ⚠️ 已注入过');
    }

    // ============================================
    // 1.5 修改 browser_click - DOM 点击工具延时
    // ============================================
    console.log('   1.5 修改 browser_click (DOM点击延时)...');

    const hasClickPatch = content.includes('[CUSTOM] browser_click:');

    // 模式1: 通过 selector 点击 - page.click(selector)
    const clickSelectorPattern = /browser_click:\s*async\s*\(args\)\s*=>\s*\{[\s\S]*?await\s+page\.click\(args\.(?:selector|element)\s*(?:,\s*\{[^}]*\})?\s*\);/g;
    if (clickSelectorPattern.test(content) && !hasClickPatch) {
        content = content.replace(
            /browser_click:\s*async\s*\(args\)\s*=>\s*\{([\s\S]*?)await\s+page\.click\(args\.(selector|element)\s*(?:,\s*\{[^}]*\})?\s*\);/g,
            `browser_click: async (args)=>{$1// [CUSTOM] browser_click: Duration-based click
                            const clickDuration = parseInt(process.env.AGENT_PRESS_DURATION_MS) || ${PRESS_DURATION};
                            console.log(\`[CUSTOM] browser_click: \${args.$2} with duration=\${clickDuration}ms\`);
                            const elem = await page.$(args.$2);
                            if (elem) {
                                const box = await elem.boundingBox();
                                if (box) {
                                    const x = box.x + box.width / 2;
                                    const y = box.y + box.height / 2;
                                    await page.mouse.move(x, y);
                                    await page.mouse.down();
                                    await new Promise(r => setTimeout(r, clickDuration));
                                    await page.mouse.up();
                                } else {
                                    await page.click(args.$2);
                                }
                            } else {
                                await page.click(args.$2);
                            }`
        );
        modifiedCount++;
        console.log('      ✅ browser_click (selector) 修改成功');
    } else if (hasClickPatch) {
        console.log('      ⚠️ 已修改过');
    } else {
        // 模式2: 通过坐标点击 - page.mouse.click(x, y)
        const clickCoordPattern = /await\s+page\.mouse\.click\(\s*(?:args\.)?(?:x|coordinates?\.x)\s*,\s*(?:args\.)?(?:y|coordinates?\.y)\s*\)/g;
        if (clickCoordPattern.test(content) && !content.includes('[CUSTOM] mouse.click duration')) {
            content = content.replace(
                /await\s+page\.mouse\.click\(\s*((?:args\.)?(?:x|coordinates?\.x))\s*,\s*((?:args\.)?(?:y|coordinates?\.y))\s*\)/g,
                `// [CUSTOM] mouse.click duration-based
                            const _clickDur = parseInt(process.env.AGENT_PRESS_DURATION_MS) || ${PRESS_DURATION};
                            await page.mouse.move($1, $2);
                            await page.mouse.down();
                            await new Promise(r => setTimeout(r, _clickDur));
                            await page.mouse.up()`
            );
            modifiedCount++;
            console.log('      ✅ browser_click (coordinates) 修改成功');
        } else {
            console.log('      ℹ️ 未找到 browser_click 匹配模式或已修改');
        }
    }

    // ============================================
    // 1.8 修改 handleClick/handleDoubleClick/handleRightClick - GUI视觉工具等待时间
    // ============================================
    console.log('   1.8 修改 GUI 视觉工具等待时间 (handleClick 等)...');

    const hasGuiWaitPatch = content.includes('[CUSTOM-GUI-WAIT]');

    if (!hasGuiWaitPatch) {
        // 替换 handleClick 中 click 后的 sleep(800)
        // 原代码：await (0, utils_namespaceObject.sleep)(800);
        //         this.logger.info('Click completed');
        let guiModified = 0;

        // handleClick 中的 sleep(800)
        const clickSleepPattern = /await\s*\(0,\s*utils_namespaceObject\.sleep\)\s*\(\s*800\s*\);\s*\n(\s*)this\.logger\.info\s*\(\s*['"]Click completed['"]\s*\)/g;
        if (clickSleepPattern.test(content)) {
            content = content.replace(
                clickSleepPattern,
                `// [CUSTOM-GUI-WAIT] Configurable click wait time
                    const _guiClickWait = parseInt(process.env.AGENT_CLICK_WAIT_MS) || 0;
                    if (_guiClickWait > 0) await (0, utils_namespaceObject.sleep)(_guiClickWait);
$1this.logger.info('Click completed')`
            );
            guiModified++;
        }

        // handleDoubleClick 中的 sleep(800)
        const doubleClickSleepPattern = /await\s*\(0,\s*utils_namespaceObject\.sleep\)\s*\(\s*800\s*\);\s*\n(\s*)this\.logger\.info\s*\(\s*['"]Double click completed['"]\s*\)/g;
        if (doubleClickSleepPattern.test(content)) {
            content = content.replace(
                doubleClickSleepPattern,
                `// [CUSTOM-GUI-WAIT] Configurable double click wait time
                    const _guiDblClickWait = parseInt(process.env.AGENT_CLICK_WAIT_MS) || 0;
                    if (_guiDblClickWait > 0) await (0, utils_namespaceObject.sleep)(_guiDblClickWait);
$1this.logger.info('Double click completed')`
            );
            guiModified++;
        }

        // handleRightClick 中的 sleep(800)
        const rightClickSleepPattern = /await\s*\(0,\s*utils_namespaceObject\.sleep\)\s*\(\s*800\s*\);\s*\n(\s*)this\.logger\.info\s*\(\s*['"]Right click completed['"]\s*\)/g;
        if (rightClickSleepPattern.test(content)) {
            content = content.replace(
                rightClickSleepPattern,
                `// [CUSTOM-GUI-WAIT] Configurable right click wait time
                    const _guiRightClickWait = parseInt(process.env.AGENT_CLICK_WAIT_MS) || 0;
                    if (_guiRightClickWait > 0) await (0, utils_namespaceObject.sleep)(_guiRightClickWait);
$1this.logger.info('Right click completed')`
            );
            guiModified++;
        }

        if (guiModified > 0) {
            modifiedCount += guiModified;
            console.log(`      ✅ GUI 视觉工具等待时间修改成功 (${guiModified} 处)`);
        } else {
            console.log('      ℹ️ 未找到 GUI 视觉工具等待时间匹配模式');
        }
    } else {
        console.log('      ⚠️ 已修改过');
    }

    // ============================================
    // 1.9 修改 browser_vision_control 工具内部的 sleep(500)
    // ============================================
    console.log('   1.9 修改 browser_vision_control 内部等待时间...');

    const hasVisionControlPatch = content.includes('[CUSTOM-VISION-WAIT]');

    if (!hasVisionControlPatch) {
        // 原代码：
        // this.logger.debug('Browser action completed', operatorResult);
        // await sleep(500);
        // return {
        //     success: true,
        const visionSleepPattern = /this\.logger\.debug\s*\(\s*['"]Browser action completed['"]\s*,\s*operatorResult\s*\);\s*\n(\s*)await\s+sleep\s*\(\s*500\s*\)/g;
        if (visionSleepPattern.test(content)) {
            content = content.replace(
                visionSleepPattern,
                `this.logger.debug('Browser action completed', operatorResult);
$1// [CUSTOM-VISION-WAIT] Configurable vision control wait time
$1const _visionWait = parseInt(process.env.AGENT_CLICK_WAIT_MS) || 0;
$1if (_visionWait > 0) await sleep(_visionWait)`
            );
            modifiedCount++;
            console.log('      ✅ browser_vision_control sleep(500) 修改成功');
        } else {
            console.log('      ℹ️ 未找到 browser_vision_control sleep(500) 匹配模式');
        }
    } else {
        console.log('      ⚠️ 已修改过');
    }

    // ============================================
    // 1.10 修改 browser_navigate 工具 - 添加页面加载等待时间
    // ============================================
    console.log('   1.10 修改 browser_navigate (页面加载等待时间)...');

    const hasNavigateWaitPatch = content.includes('[CUSTOM-NAV-WAIT]');

    if (!hasNavigateWaitPatch) {
        // 原代码：
        // await page.goto(args.url);
        // logger.info('navigateTo complete');
        // 修改为在 logger.info 之后添加等待时间
        const navigatePattern = /await\s+page\.goto\s*\(\s*args\.url\s*\)\s*;\s*\n(\s*)logger\.info\s*\(\s*['"]navigateTo complete['"]\s*\)/g;
        if (navigatePattern.test(content)) {
            content = content.replace(
                navigatePattern,
                `await page.goto(args.url);
$1logger.info('navigateTo complete');
$1// [CUSTOM-NAV-WAIT] Configurable navigate wait time for page load
$1const _navWait = parseInt(process.env.AGENT_NAVIGATE_WAIT_MS) || 0;
$1if (_navWait > 0) {
$1    console.log('[CUSTOM-NAV-WAIT] Waiting ' + _navWait + 'ms after page load...');
$1    await new Promise(r => setTimeout(r, _navWait));
$1}`
            );
            modifiedCount++;
            console.log('      ✅ browser_navigate 等待时间修改成功');
        } else {
            console.log('      ℹ️ 未找到 browser_navigate 匹配模式');
        }
    } else {
        console.log('      ⚠️ 已修改过');
    }

    // ============================================
    // 2. Agent Loop Start - 仅创建 CDP Client（不暂停）
    // ============================================
    console.log('   2. Agent Loop Start (创建CDP Client)...');

    if ((content.includes("console.log('Agent Loop Start')") || content.includes('console.log("Agent Loop Start")')) && !hasPausePatch) {
        const initInjection = `console.log('Agent Loop Start');
                // [CUSTOM-PATCH] pausePage injection - 仅在第一次创建 CDP Client
                try {
                    if (this.browserOperator && typeof global !== 'undefined') {
                        global.__CUSTOM_BROWSER_OPERATOR__ = this.browserOperator;
                    }
                    
                    const pauseEnabled = process.env.AGENT_PAUSE_ENABLED !== 'false';
                    const existingClient = typeof global !== 'undefined' && global.__CUSTOM_CDP_CLIENT__;
                    console.log('[CUSTOM-PATCH] Agent Loop Start: existingClient=' + !!existingClient);
                    
                    // [SCREENCAST-PATCH] 初始化录屏器（CDP 非阻塞版本，singleton）
                    const recordEnabled = process.env.AGENT_RECORD_ENABLED === 'true';
                    if (recordEnabled && typeof global !== 'undefined') {
                        if (!global.__SCREENCAST_RECORDER_CLASS__) {
                            try {
                                const _baseDir = require('path').dirname('${SCREENCAST_RECORDER_PATH.replace(/\\/g, '\\\\')}');
                                const _defaultPath = require('path').join(_baseDir, 'screencast-recorder-cdp.js');
                                const recorderPath = process.env.AGENT_RECORD_MODULE_PATH || _defaultPath;
                                if (require('fs').existsSync(recorderPath)) {
                                    global.__SCREENCAST_RECORDER_CLASS__ = require(recorderPath).ScreencastRecorder;
                                    console.log('[SCREENCAST-PATCH] Recorder class loaded from:', recorderPath);
                                }
                            } catch (recErr) {
                                console.log('[SCREENCAST-PATCH] Failed to load recorder:', recErr.message);
                            }
                        }
                        // Create or reconfigure recorder with per-session outputDir
                        if (global.__SCREENCAST_RECORDER_CLASS__) {
                            if (!sessionId) throw new Error('[SCREENCAST-PATCH] sessionId not passed to onEachAgentLoopStart');
                            if (!global.__SESSION_OUTPUT_DIRS__ || !global.__SESSION_OUTPUT_DIRS__[sessionId]) throw new Error('[SCREENCAST-PATCH] outputDir not set for session ' + sessionId);
                            const _sessDir = global.__SESSION_OUTPUT_DIRS__[sessionId];
                            if (!global.__CUSTOM_SCREENCAST_RECORDER__) {
                                global.__CUSTOM_SCREENCAST_RECORDER__ = new global.__SCREENCAST_RECORDER_CLASS__();
                                console.log('[SCREENCAST-PATCH] Recorder created');
                            }
                            global.__CUSTOM_SCREENCAST_RECORDER__.outputDir = require('path').join(_sessDir, 'screencast_frames');
                            console.log('[SCREENCAST-PATCH] Recorder outputDir set to:', global.__CUSTOM_SCREENCAST_RECORDER__.outputDir);
                        }
                    }
                    
                    if (!pauseEnabled) {
                        console.log('[CUSTOM-PATCH] Pause disabled (but CDP client and select badges still active)');
                    }
                    
                    // CDP client 和 select 标签功能始终运行，不受 pauseEnabled 影响
                    // [CASE-SWITCH-FIX] 检测是否发生 Case 切换（页面被重置为 about:blank）
                    let needCreateClient = !existingClient;
                    if (existingClient && this.browserOperator) {
                        try {
                            const checkPage = await this.browserOperator.getActivePage();
                            if (checkPage) {
                                const currentUrl = await checkPage.url();
                                if (currentUrl === 'about:blank' || currentUrl === 'chrome://newtab/') {
                                    console.log('[CUSTOM-PATCH] Detected case switch (page is ' + currentUrl + '), recreating CDP client...');
                                    // 清理旧的 CDP client 和录屏器
                                    global.__CUSTOM_CDP_CLIENT__ = null;
                                    global.__CUSTOM_PAGE__ = null;
                                    if (global.__CUSTOM_SCREENCAST_RECORDER__) {
                                        try { await global.__CUSTOM_SCREENCAST_RECORDER__.stopRecording(); } catch (e) {}
                                        global.__CUSTOM_SCREENCAST_RECORDER__ = null;
                                    }
                                    needCreateClient = true;
                                }
                            }
                        } catch (urlErr) {
                            console.log('[CUSTOM-PATCH] Failed to check page URL:', urlErr.message);
                        }
                    }
                    
                    if (needCreateClient) {
                        // 第一次或 Case 切换后：创建 CDP client
                        console.log('[CUSTOM-PATCH] Creating CDP client (first loop or case switch)...');
                        const page = this.browserOperator ? await this.browserOperator.getActivePage() : null;
                        if (page) {
                            const client = await page.target().createCDPSession();
                            await client.send('Animation.enable');
                            
                            // 使用 addScriptToEvaluateOnNewDocument 在每次页面加载前注入定时器拦截
                            await client.send('Page.enable');
                            
                            // [DIALOG-SELECT-PATCH] 初始化对话框历史记录
                            if (typeof global !== 'undefined') {
                                if (!global.__CUSTOM_DIALOG_HISTORY__) {
                                    global.__CUSTOM_DIALOG_HISTORY__ = [];
                                }
                                if (!global.__CUSTOM_LAST_DIALOG_CHECK__) {
                                    global.__CUSTOM_LAST_DIALOG_CHECK__ = 0;
                                }
                            }
                            
                            // [DIALOG-SELECT-PATCH] 监听 JavaScript 对话框
                            client.on('Page.javascriptDialogOpening', async (params) => {
                                const dialogInfo = {
                                    type: params.type,
                                    message: params.message,
                                    defaultPrompt: params.defaultPrompt,
                                    timestamp: Date.now(),
                                    action: null,
                                    promptText: null
                                };
                                
                                console.log(\`[DIALOG-HANDLER] Dialog detected - Type: \${params.type}, Message: "\${params.message}"\`);
                                
                                let accept = true;
                                let promptText = '';
                                
                                switch (params.type) {
                                    case 'alert':
                                        accept = true;
                                        dialogInfo.action = 'accepted';
                                        break;
                                    case 'confirm':
                                        accept = process.env.AGENT_DIALOG_CONFIRM_DEFAULT !== 'false';
                                        dialogInfo.action = accept ? 'accepted' : 'dismissed';
                                        break;
                                    case 'prompt':
                                        accept = true;
                                        promptText = process.env.AGENT_DIALOG_PROMPT_DEFAULT || params.defaultPrompt || '';
                                        dialogInfo.action = 'accepted';
                                        dialogInfo.promptText = promptText;
                                        break;
                                    case 'beforeunload':
                                        accept = true;
                                        dialogInfo.action = 'accepted';
                                        break;
                                    default:
                                        accept = true;
                                        dialogInfo.action = 'accepted';
                                }
                                
                                if (typeof global !== 'undefined' && global.__CUSTOM_DIALOG_HISTORY__) {
                                    global.__CUSTOM_DIALOG_HISTORY__.push(dialogInfo);
                                    if (global.__CUSTOM_DIALOG_HISTORY__.length > 20) {
                                        global.__CUSTOM_DIALOG_HISTORY__.shift();
                                    }
                                }
                                
                                try {
                                    await client.send('Page.handleJavaScriptDialog', {
                                        accept: accept,
                                        promptText: promptText
                                    });
                                    console.log(\`[DIALOG-HANDLER] Dialog handled: \${dialogInfo.action}\`);
                                    
                                    // [DIALOG-SELECT-PATCH] 在页面上显示角标
                                    const pg = this.browserOperator ? await this.browserOperator.getActivePage() : null;
                                    if (pg) {
                                        try {
                                            await pg.evaluate((dInfo) => {
                                                const old = document.getElementById('__agent_dialog_badge__');
                                                if (old) old.remove();
                                                
                                                const badge = document.createElement('div');
                                                badge.id = '__agent_dialog_badge__';
                                                badge.style.cssText = \`
                                                    position: fixed;
                                                    bottom: 10px;
                                                    right: 10px;
                                                    background: rgba(255, 107, 107, 0.95);
                                                    color: white;
                                                    padding: 8px 12px;
                                                    border-radius: 4px;
                                                    font-size: 12px;
                                                    z-index: 999999;
                                                    max-width: 300px;
                                                    box-shadow: 0 2px 8px rgba(0,0,0,0.2);
                                                    font-family: monospace;
                                                    pointer-events: none;
                                                \`;
                                                badge.innerHTML = \`
                                                    <div style="font-weight: bold; margin-bottom: 4px;">🔔 Dialog Handled</div>
                                                    <div>Type: \${dInfo.type}</div>
                                                    <div>Action: \${dInfo.action}</div>
                                                    <div style="margin-top: 4px; font-size: 11px; opacity: 0.9;">
                                                        "\${dInfo.message.substring(0, 50)}\${dInfo.message.length > 50 ? '...' : ''}"
                                                    </div>
                                                \`;
                                                document.body.appendChild(badge);
                                            }, dialogInfo);
                                            console.log('[DIALOG-HANDLER] Badge displayed on page');
                                        } catch (e) {
                                            console.log('[DIALOG-HANDLER] Failed to show badge:', e.message);
                                        }
                                    }
                                } catch (error) {
                                    console.error('[DIALOG-HANDLER] Error handling dialog:', error);
                                }
                            });
                            
                            await client.send('Page.addScriptToEvaluateOnNewDocument', {
                                source: \`
                                    (function() {
                                        if (window.__CUSTOM_TIMER_PATCH__) return;
                                        window.__CUSTOM_TIMER_PATCH__ = true;
                                        window.__CUSTOM_PAUSED__ = false;
                                        
                                        const origSetInterval = window.setInterval.bind(window);
                                        const origClearInterval = window.clearInterval.bind(window);
                                        const origSetTimeout = window.setTimeout.bind(window);
                                        const origClearTimeout = window.clearTimeout.bind(window);
                                        const origRAF = window.requestAnimationFrame.bind(window);
                                        const origCancelRAF = window.cancelAnimationFrame.bind(window);
                                        
                                        const intervals = new Map();
                                        const timeouts = new Map();
                                        let rafPending = [];
                                        let idCounter = 900000;
                                        
                                        window.setInterval = function(fn, delay, ...args) {
                                            const id = idCounter++;
                                            if (!window.__CUSTOM_PAUSED__) {
                                                const realId = origSetInterval(fn, delay, ...args);
                                                intervals.set(id, { fn, delay, args, realId, type: 'active' });
                                            } else {
                                                intervals.set(id, { fn, delay, args, realId: null, type: 'paused' });
                                            }
                                            return id;
                                        };
                                        
                                        window.clearInterval = function(id) {
                                            const info = intervals.get(id);
                                            if (info && info.realId !== null) origClearInterval(info.realId);
                                            intervals.delete(id);
                                        };
                                        
                                        window.setTimeout = function(fn, delay, ...args) {
                                            const id = idCounter++;
                                            const startTime = Date.now();
                                            if (!window.__CUSTOM_PAUSED__) {
                                                const realId = origSetTimeout(() => {
                                                    timeouts.delete(id);
                                                    fn.apply(null, args);
                                                }, delay);
                                                timeouts.set(id, { fn, delay, args, realId, startTime, remaining: delay });
                                            } else {
                                                timeouts.set(id, { fn, delay, args, realId: null, startTime: null, remaining: delay });
                                            }
                                            return id;
                                        };
                                        
                                        window.clearTimeout = function(id) {
                                            const info = timeouts.get(id);
                                            if (info && info.realId !== null) origClearTimeout(info.realId);
                                            timeouts.delete(id);
                                        };
                                        
                                        window.requestAnimationFrame = function(cb) {
                                            if (window.__CUSTOM_PAUSED__) {
                                                rafPending.push(cb);
                                                return -1;
                                            }
                                            return origRAF(cb);
                                        };
                                        
                                        window.__pauseAllTimers__ = function() {
                                            window.__CUSTOM_PAUSED__ = true;
                                            let cnt = 0;
                                            intervals.forEach((info, id) => {
                                                if (info.realId !== null) {
                                                    origClearInterval(info.realId);
                                                    info.realId = null;
                                                    info.type = 'paused';
                                                    cnt++;
                                                }
                                            });
                                            // [FIX] 暂停正在运行的 timeouts
                                            let timeoutCnt = 0;
                                            timeouts.forEach((info, id) => {
                                                if (info.realId !== null) {
                                                    origClearTimeout(info.realId);
                                                    // 计算剩余时间
                                                    const elapsed = Date.now() - info.startTime;
                                                    info.remaining = Math.max(0, info.delay - elapsed);
                                                    info.realId = null;
                                                    timeoutCnt++;
                                                }
                                            });
                                            console.log('[PAGE-PATCH] Paused ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                        };
                                        
                                        window.__resumeAllTimers__ = function() {
                                            window.__CUSTOM_PAUSED__ = false;
                                            let cnt = 0;
                                            intervals.forEach((info, id) => {
                                                if (info.type === 'paused') {
                                                    info.realId = origSetInterval(info.fn, info.delay, ...info.args);
                                                    info.type = 'active';
                                                    cnt++;
                                                }
                                            });
                                            // [FIX] 恢复暂停的 timeouts（使用剩余时间）
                                            let timeoutCnt = 0;
                                            timeouts.forEach((info, id) => {
                                                if (info.realId === null && info.fn) {
                                                    const remaining = info.remaining || info.delay || 0;
                                                    info.startTime = Date.now();
                                                    info.realId = origSetTimeout(() => {
                                                        timeouts.delete(id);
                                                        info.fn.apply(null, info.args || []);
                                                    }, remaining);
                                                    timeoutCnt++;
                                                }
                                            });
                                            rafPending.forEach(cb => origRAF(cb));
                                            rafPending = [];
                                            console.log('[PAGE-PATCH] Resumed ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                        };
                                        
                                        // [DIALOG-SELECT-PATCH] 标记 select 和 date/time 输入框并显示可见标签
                                        function markAllSelects() {
                                            // [CLEANUP] 先清理孤儿 badge（对应的 input/select 已被移除）
                                            document.querySelectorAll('.__agent_date_badge__').forEach(badge => {
                                                const dateId = badge.dataset.dateId;
                                                const inp = document.querySelector('[data-badge-id=\"' + dateId + '\"]');
                                                if (!inp || !document.body.contains(inp)) {
                                                    badge.remove();
                                                }
                                            });
                                            document.querySelectorAll('.__agent_select_badge__').forEach(badge => {
                                                const selectId = badge.dataset.selectId;
                                                const sel = document.querySelector('[data-badge-id=\"' + selectId + '\"]');
                                                if (!sel || !document.body.contains(sel)) {
                                                    badge.remove();
                                                }
                                            });
                                            
                                            // 标记日期/时间输入框
                                            document.querySelectorAll('input[type="date"], input[type="time"], input[type="datetime-local"], input[type="month"], input[type="week"]').forEach((inp, idx) => {
                                                if (!inp.dataset.agentDateMarked) {
                                                    inp.dataset.agentDateMarked = 'true';
                                                    
                                                    // 生成选择器
                                                    let selector = '';
                                                    if (inp.id) {
                                                        selector = '#' + inp.id;
                                                    } else if (inp.name) {
                                                        selector = 'input[name=\"' + inp.name + '\"]';
                                                    } else {
                                                        const uniqueId = 'agent_date_' + idx + '_' + Math.random().toString(36).substr(2, 5);
                                                        inp.setAttribute('data-agent-id', uniqueId);
                                                        selector = 'input[data-agent-id=\"' + uniqueId + '\"]';
                                                    }
                                                    inp.dataset.agentSelector = selector;
                                                    
                                                    const inputType = inp.type;
                                                    let formatHint = '';
                                                    switch(inputType) {
                                                        case 'date': formatHint = 'YYYY-MM-DD'; break;
                                                        case 'time': formatHint = 'HH:MM'; break;
                                                        case 'datetime-local': formatHint = 'YYYY-MM-DDTHH:MM'; break;
                                                        case 'month': formatHint = 'YYYY-MM'; break;
                                                        case 'week': formatHint = 'YYYY-Www'; break;
                                                    }
                                                    
                                                    const badge = document.createElement('div');
                                                    badge.className = '__agent_date_badge__';
                                                    badge.style.cssText = 'position:absolute;background:rgba(75,0,130,0.95);color:white;padding:2px 6px;border-radius:3px;font-size:10px;font-family:monospace;z-index:999999;pointer-events:none;white-space:nowrap;max-width:400px;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 3px rgba(0,0,0,0.3);';
                                                    
                                                    const currentVal = inp.value || '(empty)';
                                                    badge.textContent = '📅 [' + selector + '] ' + currentVal + ' | Format: ' + formatHint;
                                                    badge.dataset.dateId = 'datebadge_' + Math.random().toString(36).substr(2, 9);
                                                    inp.dataset.badgeId = badge.dataset.dateId;
                                                    
                                                    const rect = inp.getBoundingClientRect();
                                                    badge.style.position = 'fixed';
                                                    badge.style.left = rect.left + 'px';
                                                    badge.style.top = (rect.top - 18) + 'px';
                                                    
                                                    document.body.appendChild(badge);
                                                    
                                                    inp.addEventListener('change', function() {
                                                        const b = document.querySelector('[data-date-id=\"' + this.dataset.badgeId + '\"]');
                                                        if (b) {
                                                            const newVal = this.value || '(empty)';
                                                            b.textContent = '📅 [' + this.dataset.agentSelector + '] ' + newVal + ' | Format: ' + formatHint;
                                                        }
                                                    });
                                                }
                                            });
                                            
                                            // 更新日期输入框标签位置
                                            document.querySelectorAll('input[data-agent-date-marked]').forEach(inp => {
                                                const badge = document.querySelector('[data-date-id=\"' + inp.dataset.badgeId + '\"]');
                                                if (badge) {
                                                    const rect = inp.getBoundingClientRect();
                                                    badge.style.left = rect.left + 'px';
                                                    badge.style.top = (rect.top - 18) + 'px';
                                                }
                                            });
                                            
                                            // 标记 select 元素
                                            document.querySelectorAll('select').forEach((sel, idx) => {
                                                if (!sel.dataset.agentSelectMarked) {
                                                    const options = Array.from(sel.options).map(o => ({
                                                        value: o.value,
                                                        text: o.textContent.trim(),
                                                        selected: o.selected
                                                    }));
                                                    
                                                    // 生成唯一选择器
                                                    let selector = '';
                                                    if (sel.id) {
                                                        selector = '#' + sel.id;
                                                    } else if (sel.name) {
                                                        selector = 'select[name=\"' + sel.name + '\"]';
                                                    } else {
                                                        // 添加自定义属性作为选择器
                                                        const uniqueId = 'agent_sel_' + idx + '_' + Math.random().toString(36).substr(2, 5);
                                                        sel.setAttribute('data-agent-id', uniqueId);
                                                        selector = 'select[data-agent-id=\"' + uniqueId + '\"]';
                                                    }
                                                    
                                                    sel.dataset.agentSelectMarked = 'true';
                                                    sel.dataset.agentSelectOptions = JSON.stringify(options);
                                                    sel.dataset.agentCurrentValue = sel.value;
                                                    sel.dataset.agentSelector = selector;
                                                    
                                                    // 创建可见标签
                                                    const badge = document.createElement('div');
                                                    badge.className = '__agent_select_badge__';
                                                    badge.style.cssText = 'position:absolute;background:rgba(255,140,0,0.95);color:white;padding:2px 6px;border-radius:3px;font-size:10px;font-family:monospace;z-index:999999;pointer-events:none;white-space:nowrap;max-width:350px;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 3px rgba(0,0,0,0.3);';
                                                    
                                                    const currentText = sel.options[sel.selectedIndex]?.text || sel.value || '(empty)';
                                                    const optionTexts = options.slice(0, 3).map(o => o.text).join(', ');
                                                    const moreCount = options.length > 3 ? ' +' + (options.length - 3) + ' more' : '';
                                                    badge.textContent = '🔽 [' + selector + '] ' + currentText + ' | ' + optionTexts + moreCount;
                                                    badge.dataset.selectId = 'badge_' + Math.random().toString(36).substr(2, 9);
                                                    sel.dataset.badgeId = badge.dataset.selectId;
                                                    
                                                    // 定位标签
                                                    const rect = sel.getBoundingClientRect();
                                                    badge.style.position = 'fixed';
                                                    badge.style.left = rect.left + 'px';
                                                    badge.style.top = (rect.top - 18) + 'px';
                                                    
                                                    document.body.appendChild(badge);
                                                    
                                                    // 监听变化更新标签
                                                    sel.addEventListener('change', function() {
                                                        this.dataset.agentCurrentValue = this.value;
                                                        const b = document.querySelector('[data-select-id=\"' + this.dataset.badgeId + '\"]');
                                                        if (b) {
                                                            const newText = this.options[this.selectedIndex]?.text || this.value || '(empty)';
                                                            const opts = JSON.parse(this.dataset.agentSelectOptions || '[]');
                                                            const optTexts = opts.slice(0, 3).map(o => o.text).join(', ');
                                                            const more = opts.length > 3 ? ' +' + (opts.length - 3) + ' more' : '';
                                                            const selSelector = this.dataset.agentSelector || '';
                                                            b.textContent = '🔽 [' + selSelector + '] ' + newText + ' | ' + optTexts + more;
                                                        }
                                                    });
                                                }
                                            });
                                            
                                            // 更新标签位置（处理滚动和布局变化）
                                            document.querySelectorAll('select[data-agent-select-marked]').forEach(sel => {
                                                const badge = document.querySelector('[data-select-id=\"' + sel.dataset.badgeId + '\"]');
                                                if (badge) {
                                                    const rect = sel.getBoundingClientRect();
                                                    badge.style.left = rect.left + 'px';
                                                    badge.style.top = (rect.top - 18) + 'px';
                                                }
                                            });
                                        }
                                        
                                        function setupSelectObserver() {
                                            if (document.documentElement) {
                                                new MutationObserver(() => markAllSelects()).observe(document.documentElement, {
                                                    childList: true,
                                                    subtree: true
                                                });
                                                console.log('[PAGE-PATCH] MutationObserver installed');
                                            } else {
                                                console.log('[PAGE-PATCH] documentElement not ready, will retry');
                                            }
                                        }
                                        
                                        // [SCROLL-FIX] 更新所有 badge 位置的函数
                                        function updateAllBadgePositions() {
                                            // 更新 select badges
                                            document.querySelectorAll('select[data-agent-select-marked]').forEach(sel => {
                                                const badge = document.querySelector('[data-select-id=\"' + sel.dataset.badgeId + '\"]');
                                                if (badge && document.body.contains(sel)) {
                                                    const rect = sel.getBoundingClientRect();
                                                    badge.style.left = rect.left + 'px';
                                                    badge.style.top = (rect.top - 18) + 'px';
                                                }
                                            });
                                            // 更新 date badges
                                            document.querySelectorAll('input[data-agent-date-marked]').forEach(inp => {
                                                const badge = document.querySelector('[data-date-id=\"' + inp.dataset.badgeId + '\"]');
                                                if (badge && document.body.contains(inp)) {
                                                    const rect = inp.getBoundingClientRect();
                                                    badge.style.left = rect.left + 'px';
                                                    badge.style.top = (rect.top - 18) + 'px';
                                                }
                                            });
                                        }
                                        
                                        // [SCROLL-FIX] 添加滚动监听器
                                        function setupScrollListener() {
                                            if (window.__CUSTOM_SCROLL_LISTENER__) return;
                                            window.__CUSTOM_SCROLL_LISTENER__ = true;
                                            
                                            // 使用 passive 和 throttle 优化性能
                                            let scrollTimeout = null;
                                            const throttledUpdate = () => {
                                                if (scrollTimeout) return;
                                                scrollTimeout = setTimeout(() => {
                                                    updateAllBadgePositions();
                                                    scrollTimeout = null;
                                                }, 16); // ~60fps
                                            };
                                            
                                            // 监听 window 滚动
                                            window.addEventListener('scroll', throttledUpdate, { passive: true });
                                            // 监听所有可滚动容器（捕获阶段）
                                            document.addEventListener('scroll', throttledUpdate, { passive: true, capture: true });
                                            // 监听 resize 事件
                                            window.addEventListener('resize', throttledUpdate, { passive: true });
                                            
                                            console.log('[PAGE-PATCH] Scroll listener installed for badge position updates');
                                        }
                                        
                                        if (document.readyState === 'loading') {
                                            document.addEventListener('DOMContentLoaded', () => {
                                                markAllSelects();
                                                setupSelectObserver();
                                                setupScrollListener();
                                            });
                                        } else {
                                            markAllSelects();
                                            setupSelectObserver();
                                            setupScrollListener();
                                        }
                                        
                                        console.log('[PAGE-PATCH] Timer interception and select marker installed');
                                    })();
                                \`
                            });
                            
                            // 如果脚本是新安装的，需要刷新页面让拦截生效
                            // [FIX] 但如果页面是 about:blank，跳过 reload（无意义且可能导致卡住）
                            const _currentPageUrl = await page.url();
                            const _isBlankPage = !_currentPageUrl || _currentPageUrl === 'about:blank' || _currentPageUrl === 'chrome://newtab/';
                            if (!_isBlankPage) {
                                console.log('[CUSTOM-PATCH] Reloading page to activate timer interception...');
                                await page.reload({ waitUntil: 'domcontentloaded' });
                                console.log('[CUSTOM-PATCH] Page reloaded with timer interception active');
                            } else {
                                console.log('[CUSTOM-PATCH] Skipping ALL page.evaluate() for blank page (' + _currentPageUrl + '), will activate on next navigation');
                            }

                            // [PERF-FIX] 空白页跳过所有 page.evaluate()，减少不必要的 overhead
                            let installResult = _isBlankPage ? 'skipped for blank page' : await page.evaluate(() => {
                                if (window.__CUSTOM_TIMER_PATCH__) return 'already installed';
                                
                                window.__CUSTOM_TIMER_PATCH__ = true;
                                window.__CUSTOM_PAUSED__ = false;
                                
                                const origSetInterval = window.setInterval.bind(window);
                                const origClearInterval = window.clearInterval.bind(window);
                                const origSetTimeout = window.setTimeout.bind(window);
                                const origClearTimeout = window.clearTimeout.bind(window);
                                const origRAF = window.requestAnimationFrame.bind(window);
                                
                                const intervals = new Map();
                                const timeouts = new Map();
                                let rafPending = [];
                                let idCounter = 900000;
                                
                                // 拦截 setInterval
                                window.setInterval = function(fn, delay, ...args) {
                                    const id = idCounter++;
                                    if (!window.__CUSTOM_PAUSED__) {
                                        const realId = origSetInterval(fn, delay, ...args);
                                        intervals.set(id, { fn, delay, args, realId, type: 'active' });
                                    } else {
                                        intervals.set(id, { fn, delay, args, realId: null, type: 'paused' });
                                    }
                                    return id;
                                };
                                
                                window.clearInterval = function(id) {
                                    const info = intervals.get(id);
                                    if (info && info.realId !== null) origClearInterval(info.realId);
                                    intervals.delete(id);
                                };
                                
                                // 拦截 setTimeout
                                window.setTimeout = function(fn, delay, ...args) {
                                    const id = idCounter++;
                                    const startTime = Date.now();
                                    if (!window.__CUSTOM_PAUSED__) {
                                        const realId = origSetTimeout(() => {
                                            timeouts.delete(id);
                                            fn.apply(null, args);
                                        }, delay);
                                        timeouts.set(id, { fn, delay, args, realId, startTime, remaining: delay });
                                    } else {
                                        timeouts.set(id, { fn, delay, args, realId: null, startTime: null, remaining: delay });
                                    }
                                    return id;
                                };
                                
                                window.clearTimeout = function(id) {
                                    const info = timeouts.get(id);
                                    if (info && info.realId !== null) origClearTimeout(info.realId);
                                    timeouts.delete(id);
                                };
                                
                                // 拦截 requestAnimationFrame
                                window.requestAnimationFrame = function(cb) {
                                    if (window.__CUSTOM_PAUSED__) {
                                        rafPending.push(cb);
                                        return -1;
                                    }
                                    return origRAF(cb);
                                };
                                
                                window.__pauseAllTimers__ = function() {
                                    window.__CUSTOM_PAUSED__ = true;
                                    let cnt = 0;
                                    intervals.forEach((info, id) => {
                                        if (info.realId !== null) {
                                            origClearInterval(info.realId);
                                            info.realId = null;
                                            info.type = 'paused';
                                            cnt++;
                                        }
                                    });
                                    // 暂停正在运行的 timeouts
                                    let timeoutCnt = 0;
                                    timeouts.forEach((info, id) => {
                                        if (info.realId !== null) {
                                            origClearTimeout(info.realId);
                                            const elapsed = Date.now() - info.startTime;
                                            info.remaining = Math.max(0, info.delay - elapsed);
                                            info.realId = null;
                                            timeoutCnt++;
                                        }
                                    });
                                    console.log('[PAGE-PATCH] Paused ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                    return cnt + timeoutCnt;
                                };
                                
                                window.__resumeAllTimers__ = function() {
                                    window.__CUSTOM_PAUSED__ = false;
                                    let cnt = 0;
                                    intervals.forEach((info, id) => {
                                        if (info.type === 'paused') {
                                            info.realId = origSetInterval(info.fn, info.delay, ...info.args);
                                            info.type = 'active';
                                            cnt++;
                                        }
                                    });
                                    // 恢复暂停的 timeouts（使用剩余时间）
                                    let timeoutCnt = 0;
                                    timeouts.forEach((info, id) => {
                                        if (info.realId === null && info.fn) {
                                            const remaining = info.remaining || info.delay || 0;
                                            info.startTime = Date.now();
                                            info.realId = origSetTimeout(() => {
                                                timeouts.delete(id);
                                                info.fn.apply(null, info.args || []);
                                            }, remaining);
                                            timeoutCnt++;
                                        }
                                    });
                                    rafPending.forEach(cb => origRAF(cb));
                                    rafPending = [];
                                    console.log('[PAGE-PATCH] Resumed ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                    return cnt + timeoutCnt;
                                };
                                
                                console.log('[PAGE-PATCH] Timer interception installed on current page');
                                return 'installed on current page';
                            });
                            console.log('[CUSTOM-PATCH] Timer install result:', installResult);
                            
                            // [SELECT-DATE-BADGE] 在当前页面也注入 markAllSelects 代码
                            // 注意：addScriptToEvaluateOnNewDocument 只在页面导航后生效
                            // 所以当前页面需要单独执行 markAllSelects
                            try {
                                await page.evaluate(() => {
                                    // [DIALOG-SELECT-PATCH] 标记 select 和 date/time 输入框并显示可见标签
                                    function markAllSelects() {
                                        // [CLEANUP] 先清理孤儿 badge（对应的 input/select 已被移除）
                                        document.querySelectorAll('.__agent_date_badge__').forEach(badge => {
                                            const dateId = badge.dataset.dateId;
                                            const inp = document.querySelector('[data-badge-id="' + dateId + '"]');
                                            if (!inp || !document.body.contains(inp)) {
                                                badge.remove();
                                            }
                                        });
                                        document.querySelectorAll('.__agent_select_badge__').forEach(badge => {
                                            const selectId = badge.dataset.selectId;
                                            const sel = document.querySelector('[data-badge-id="' + selectId + '"]');
                                            if (!sel || !document.body.contains(sel)) {
                                                badge.remove();
                                            }
                                        });
                                        
                                        // 标记日期/时间输入框
                                        document.querySelectorAll('input[type="date"], input[type="time"], input[type="datetime-local"], input[type="month"], input[type="week"]').forEach((inp, idx) => {
                                            if (!inp.dataset.agentDateMarked) {
                                                inp.dataset.agentDateMarked = 'true';
                                                
                                                let selector = '';
                                                if (inp.id) {
                                                    selector = '#' + inp.id;
                                                } else if (inp.name) {
                                                    selector = 'input[name="' + inp.name + '"]';
                                                } else {
                                                    const uniqueId = 'agent_date_' + idx + '_' + Math.random().toString(36).substr(2, 5);
                                                    inp.setAttribute('data-agent-id', uniqueId);
                                                    selector = 'input[data-agent-id="' + uniqueId + '"]';
                                                }
                                                inp.dataset.agentSelector = selector;
                                                
                                                const inputType = inp.type;
                                                let formatHint = '';
                                                switch(inputType) {
                                                    case 'date': formatHint = 'YYYY-MM-DD'; break;
                                                    case 'time': formatHint = 'HH:MM'; break;
                                                    case 'datetime-local': formatHint = 'YYYY-MM-DDTHH:MM'; break;
                                                    case 'month': formatHint = 'YYYY-MM'; break;
                                                    case 'week': formatHint = 'YYYY-Www'; break;
                                                }
                                                
                                                const badge = document.createElement('div');
                                                badge.className = '__agent_date_badge__';
                                                badge.style.cssText = 'position:fixed;background:rgba(75,0,130,0.95);color:white;padding:2px 6px;border-radius:3px;font-size:10px;font-family:monospace;z-index:999999;pointer-events:none;white-space:nowrap;max-width:400px;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 3px rgba(0,0,0,0.3);';
                                                
                                                const currentVal = inp.value || '(empty)';
                                                badge.textContent = '📅 [' + selector + '] ' + currentVal + ' | Format: ' + formatHint;
                                                badge.dataset.dateId = 'datebadge_' + Math.random().toString(36).substr(2, 9);
                                                inp.dataset.badgeId = badge.dataset.dateId;
                                                
                                                const rect = inp.getBoundingClientRect();
                                                badge.style.left = rect.left + 'px';
                                                badge.style.top = (rect.top - 18) + 'px';
                                                
                                                document.body.appendChild(badge);
                                                
                                                inp.addEventListener('change', function() {
                                                    const b = document.querySelector('[data-date-id="' + this.dataset.badgeId + '"]');
                                                    if (b) {
                                                        const newVal = this.value || '(empty)';
                                                        b.textContent = '📅 [' + this.dataset.agentSelector + '] ' + newVal + ' | Format: ' + formatHint;
                                                    }
                                                });
                                            }
                                        });
                                        
                                        // 标记 select 元素
                                        document.querySelectorAll('select').forEach((sel, idx) => {
                                            if (!sel.dataset.agentSelectMarked) {
                                                const options = Array.from(sel.options).map(o => ({
                                                    value: o.value,
                                                    text: o.textContent.trim(),
                                                    selected: o.selected
                                                }));
                                                
                                                let selector = '';
                                                if (sel.id) {
                                                    selector = '#' + sel.id;
                                                } else if (sel.name) {
                                                    selector = 'select[name="' + sel.name + '"]';
                                                } else {
                                                    const uniqueId = 'agent_sel_' + idx + '_' + Math.random().toString(36).substr(2, 5);
                                                    sel.setAttribute('data-agent-id', uniqueId);
                                                    selector = 'select[data-agent-id="' + uniqueId + '"]';
                                                }
                                                
                                                sel.dataset.agentSelectMarked = 'true';
                                                sel.dataset.agentSelectOptions = JSON.stringify(options);
                                                sel.dataset.agentCurrentValue = sel.value;
                                                sel.dataset.agentSelector = selector;
                                                
                                                const badge = document.createElement('div');
                                                badge.className = '__agent_select_badge__';
                                                badge.style.cssText = 'position:fixed;background:rgba(255,140,0,0.95);color:white;padding:2px 6px;border-radius:3px;font-size:10px;font-family:monospace;z-index:999999;pointer-events:none;white-space:nowrap;max-width:350px;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 3px rgba(0,0,0,0.3);';
                                                
                                                const currentText = sel.options[sel.selectedIndex]?.text || sel.value || '(empty)';
                                                const optionTexts = options.slice(0, 3).map(o => o.text).join(', ');
                                                const moreCount = options.length > 3 ? ' +' + (options.length - 3) + ' more' : '';
                                                badge.textContent = '🔽 [' + selector + '] ' + currentText + ' | ' + optionTexts + moreCount;
                                                badge.dataset.selectId = 'badge_' + Math.random().toString(36).substr(2, 9);
                                                sel.dataset.badgeId = badge.dataset.selectId;
                                                
                                                const rect = sel.getBoundingClientRect();
                                                badge.style.left = rect.left + 'px';
                                                badge.style.top = (rect.top - 18) + 'px';
                                                
                                                document.body.appendChild(badge);
                                                
                                                sel.addEventListener('change', function() {
                                                    this.dataset.agentCurrentValue = this.value;
                                                    const b = document.querySelector('[data-select-id="' + this.dataset.badgeId + '"]');
                                                    if (b) {
                                                        const newText = this.options[this.selectedIndex]?.text || this.value || '(empty)';
                                                        const opts = JSON.parse(this.dataset.agentSelectOptions || '[]');
                                                        const optTexts = opts.slice(0, 3).map(o => o.text).join(', ');
                                                        const more = opts.length > 3 ? ' +' + (opts.length - 3) + ' more' : '';
                                                        const selSelector = this.dataset.agentSelector || '';
                                                        b.textContent = '🔽 [' + selSelector + '] ' + newText + ' | ' + optTexts + more;
                                                    }
                                                });
                                            }
                                        });
                                        
                                        // 更新标签位置
                                        document.querySelectorAll('select[data-agent-select-marked]').forEach(sel => {
                                            const badge = document.querySelector('[data-select-id="' + sel.dataset.badgeId + '"]');
                                            if (badge) {
                                                const rect = sel.getBoundingClientRect();
                                                badge.style.left = rect.left + 'px';
                                                badge.style.top = (rect.top - 18) + 'px';
                                            }
                                        });
                                        document.querySelectorAll('input[data-agent-date-marked]').forEach(inp => {
                                            const badge = document.querySelector('[data-date-id="' + inp.dataset.badgeId + '"]');
                                            if (badge) {
                                                const rect = inp.getBoundingClientRect();
                                                badge.style.left = rect.left + 'px';
                                                badge.style.top = (rect.top - 18) + 'px';
                                            }
                                        });
                                    }
                                    
                                    function setupSelectObserver() {
                                        if (document.documentElement && !window.__CUSTOM_SELECT_OBSERVER__) {
                                            window.__CUSTOM_SELECT_OBSERVER__ = true;
                                            new MutationObserver(() => markAllSelects()).observe(document.documentElement, {
                                                childList: true,
                                                subtree: true
                                            });
                                        }
                                    }
                                    
                                    function setupScrollListener() {
                                        if (window.__CUSTOM_SCROLL_LISTENER__) return;
                                        window.__CUSTOM_SCROLL_LISTENER__ = true;
                                        
                                        let scrollTimeout = null;
                                        const throttledUpdate = () => {
                                            if (scrollTimeout) return;
                                            scrollTimeout = setTimeout(() => {
                                                // 更新所有 badge 位置
                                                document.querySelectorAll('select[data-agent-select-marked]').forEach(sel => {
                                                    const badge = document.querySelector('[data-select-id="' + sel.dataset.badgeId + '"]');
                                                    if (badge && document.body.contains(sel)) {
                                                        const rect = sel.getBoundingClientRect();
                                                        badge.style.left = rect.left + 'px';
                                                        badge.style.top = (rect.top - 18) + 'px';
                                                    }
                                                });
                                                document.querySelectorAll('input[data-agent-date-marked]').forEach(inp => {
                                                    const badge = document.querySelector('[data-date-id="' + inp.dataset.badgeId + '"]');
                                                    if (badge && document.body.contains(inp)) {
                                                        const rect = inp.getBoundingClientRect();
                                                        badge.style.left = rect.left + 'px';
                                                        badge.style.top = (rect.top - 18) + 'px';
                                                    }
                                                });
                                                scrollTimeout = null;
                                            }, 16);
                                        };
                                        
                                        window.addEventListener('scroll', throttledUpdate, { passive: true });
                                        document.addEventListener('scroll', throttledUpdate, { passive: true, capture: true });
                                        window.addEventListener('resize', throttledUpdate, { passive: true });
                                    }
                                    
                                    // 执行标记
                                    markAllSelects();
                                    setupSelectObserver();
                                    setupScrollListener();
                                    
                                    console.log('[PAGE-PATCH] Select/Date badges installed on current page');
                                    return 'badges installed';
                                });
                                console.log('[CUSTOM-PATCH] Select/Date badges result: badges installed');
                            } catch (badgeErr) {
                                console.log('[CUSTOM-PATCH] Select/Date badges failed:', badgeErr.message);
                            }
                            
                            global.__CUSTOM_CDP_CLIENT__ = client;
                            console.log('[CUSTOM-PATCH] CDP client created + timer interception registered');
                            
                            // [SCREENCAST-PATCH] 初始化录屏器并传入 CDP client 和 page
                            // [FIX] 每次 CDP client 创建时都重新初始化录屏器（支持 case 切换和第一次初始化）
                            if (global.__CUSTOM_SCREENCAST_RECORDER__ && client) {
                                try {
                                    await global.__CUSTOM_SCREENCAST_RECORDER__.init(client, 'agent_session', page);
                                    console.log('[SCREENCAST-PATCH] Recorder initialized with CDP client and page');
                                } catch (recInitErr) {
                                    console.log('[SCREENCAST-PATCH] Recorder init failed:', recInitErr.message);
                                }
                            }
                        }
                    } else {
                        // 后续循环：页面应该已经在上一轮工具执行后暂停了
                        console.log('[CUSTOM-PATCH] Page should already be paused from last tool execution');
                    }
                } catch (initErr) {
                    console.log('[CUSTOM-PATCH] Init error:', initErr.message);
                }`;

        content = content.replace(/console\.log\(['"]Agent Loop Start['"]\);?/g, initInjection);
        modifiedCount++;
        console.log('      ✅ Agent Loop Start CDP初始化注入成功');
        console.log('      📹 录屏功能钩子注入成功 (运行时设置 AGENT_RECORD_ENABLED=true 开启)');
    } else if (hasPausePatch) {
        console.log('      ⚠️ 已修改过');
    } else {
        console.log('      ❌ 未找到 Agent Loop Start');
    }

    // ============================================
    // 3. 工具执行前 - 恢复网页
    // ============================================
    console.log('   3. 工具执行前恢复网页...');

    const toolExecutingLog = /([\w.]+\.(?:debug|log|info)\s*\(\s*)`\[Tool\]\s*Executing:/;
    if (toolExecutingLog.test(content) && !content.includes('[CUSTOM-PATCH] resumePage before tool execution')) {
        content = content.replace(
            /([\w.]+\.(?:debug|log|info)\s*\(\s*)`\[Tool\]\s*Executing:/g,
            `// [CUSTOM-PATCH] resumePage before tool execution + remove dialog badge
            try {
            const pauseEnabled = process.env.AGENT_PAUSE_ENABLED !== 'false';
            if (pauseEnabled && typeof global !== 'undefined') {
                // [DIALOG-SELECT-PATCH] 移除对话框角标（下一次工具调用前）
                let pg = null;
                if (global.__CUSTOM_BROWSER_OPERATOR__) {
                    try { pg = await global.__CUSTOM_BROWSER_OPERATOR__.getActivePage(); } catch (e) { }
                }
                if (!pg) { pg = global.__CUSTOM_PAGE__; }
                if (pg) {
                    try {
                        await pg.evaluate(() => {
                            const badge = document.getElementById('__agent_dialog_badge__');
                            if (badge) {
                                badge.remove();
                                console.log('[DIALOG-HANDLER] Badge removed before tool execution');
                            }
                        });
                    } catch (e) {
                        console.log('[DIALOG-HANDLER] Failed to remove badge:', e.message);
                    }
                }
                // DOM 模式下首次执行时创建 CDP client
                if (!global.__CUSTOM_CDP_CLIENT__) {
                    console.log('[CUSTOM-PATCH] First tool - creating CDP client...');
                    let pg = null;

                    // 方法1: 尝试从 browserOperator 获取
                    const browserOp = global.__CUSTOM_BROWSER_OPERATOR__;
                    if (browserOp) {
                        try {
                            pg = await browserOp.getActivePage();
                            console.log('[CUSTOM-PATCH] Got page from browserOperator');
                        } catch (e) {
                            console.log('[CUSTOM-PATCH] browserOperator failed:', e.message);
                        }
                    }

                    // 方法2: 直接通过 puppeteer 连接 CDP
                    if (!pg) {
                        try {
                            console.log('[CUSTOM-PATCH] Trying direct puppeteer connection...');
                            const puppeteer = require('puppeteer-core');
                            const http = require('http');

                            // 获取 WebSocket URL
                            const wsUrl = await new Promise((resolve, reject) => {
                                const cdpHost = process.env.CDP_HOST || '127.0.0.1';
                                const cdpPort = process.env.CDP_PORT || '9222';
                                http.get(\`http://\${cdpHost}:\${cdpPort}/json/version\`, (res) => {
                                        let data = '';
                                        res.on('data', chunk => data += chunk);
                                        res.on('end', () => {
                                            try {
                                                const json = JSON.parse(data);
                                                resolve(json.webSocketDebuggerUrl);
                                            } catch (e) { reject(e); }
                                        });
                                    }).on('error', reject);
                                });
                                
                                console.log('[CUSTOM-PATCH] Connecting to:', wsUrl);
                                const browser = await puppeteer.connect({ browserWSEndpoint: wsUrl });
                                const pages = await browser.pages();
                                pg = pages.length > 0 ? pages[pages.length - 1] : null;
                                
                                if (pg) {
                                    global.__CUSTOM_BROWSER__ = browser;
                                    console.log('[CUSTOM-PATCH] Got page from direct puppeteer connection');
                                }
                            } catch (e) {
                                console.log('[CUSTOM-PATCH] Direct puppeteer connection failed:', e.message);
                            }
                        }
                        
                        if (pg) {
                            try {
                                const client = await pg.target().createCDPSession();
                                await client.send('Animation.enable');
                                await client.send('Page.enable');
                                global.__CUSTOM_CDP_CLIENT__ = client;
                                global.__CUSTOM_PAGE__ = pg;
                                console.log('[CUSTOM-PATCH] CDP client created');
                                
                                // 使用 addScriptToEvaluateOnNewDocument 确保后续页面导航时自动安装 timer interception
                                try {
                                    await client.send('Page.addScriptToEvaluateOnNewDocument', {
                                        source: \`
                                            (function() {
                                                if (window.__CUSTOM_TIMER_PATCH__) return;
                                                window.__CUSTOM_TIMER_PATCH__ = true;
                                                window.__CUSTOM_PAUSED__ = false;
                                                
                                                const origSetInterval = window.setInterval.bind(window);
                                                const origClearInterval = window.clearInterval.bind(window);
                                                const origSetTimeout = window.setTimeout.bind(window);
                                                const origClearTimeout = window.clearTimeout.bind(window);
                                                const origRAF = window.requestAnimationFrame.bind(window);
                                                
                                                const intervals = new Map();
                                                const timeouts = new Map();
                                                let rafPending = [];
                                                let idCounter = 900000;
                                                
                                                window.setInterval = function(fn, delay, ...args) {
                                                    const id = idCounter++;
                                                    if (!window.__CUSTOM_PAUSED__) {
                                                        const realId = origSetInterval(fn, delay, ...args);
                                                        intervals.set(id, { fn, delay, args, realId, type: 'active' });
                                                    } else {
                                                        intervals.set(id, { fn, delay, args, realId: null, type: 'paused' });
                                                    }
                                                    return id;
                                                };
                                                
                                                window.clearInterval = function(id) {
                                                    const info = intervals.get(id);
                                                    if (info && info.realId !== null) origClearInterval(info.realId);
                                                    intervals.delete(id);
                                                };
                                                
                                                window.setTimeout = function(fn, delay, ...args) {
                                                    const id = idCounter++;
                                                    const startTime = Date.now();
                                                    if (!window.__CUSTOM_PAUSED__) {
                                                        const realId = origSetTimeout(() => { timeouts.delete(id); fn.apply(null, args); }, delay);
                                                        timeouts.set(id, { fn, delay, args, realId, startTime, remaining: delay });
                                                    } else {
                                                        timeouts.set(id, { fn, delay, args, realId: null, startTime: null, remaining: delay });
                                                    }
                                                    return id;
                                                };
                                                
                                                window.clearTimeout = function(id) {
                                                    const info = timeouts.get(id);
                                                    if (info && info.realId !== null) origClearTimeout(info.realId);
                                                    timeouts.delete(id);
                                                };
                                                
                                                window.requestAnimationFrame = function(cb) {
                                                    if (window.__CUSTOM_PAUSED__) { rafPending.push(cb); return -1; }
                                                    return origRAF(cb);
                                                };
                                                
                                                window.__pauseAllTimers__ = function() {
                                                    window.__CUSTOM_PAUSED__ = true;
                                                    let cnt = 0;
                                                    intervals.forEach((info) => {
                                                        if (info.realId !== null) { origClearInterval(info.realId); info.realId = null; info.type = 'paused'; cnt++; }
                                                    });
                                                    let timeoutCnt = 0;
                                                    timeouts.forEach((info) => {
                                                        if (info.realId !== null) {
                                                            origClearTimeout(info.realId);
                                                            info.remaining = Math.max(0, info.delay - (Date.now() - info.startTime));
                                                            info.realId = null;
                                                            timeoutCnt++;
                                                        }
                                                    });
                                                    console.log('[PAGE-PATCH] Paused ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                                    return cnt + timeoutCnt;
                                                };
                                                
                                                window.__resumeAllTimers__ = function() {
                                                    window.__CUSTOM_PAUSED__ = false;
                                                    let cnt = 0;
                                                    intervals.forEach((info) => {
                                                        if (info.type === 'paused') { info.realId = origSetInterval(info.fn, info.delay, ...info.args); info.type = 'active'; cnt++; }
                                                    });
                                                    let timeoutCnt = 0;
                                                    timeouts.forEach((info, id) => {
                                                        if (info.realId === null && info.fn) {
                                                            const remaining = info.remaining || info.delay || 0;
                                                            info.startTime = Date.now();
                                                            info.realId = origSetTimeout(() => { timeouts.delete(id); info.fn.apply(null, info.args || []); }, remaining);
                                                            timeoutCnt++;
                                                        }
                                                    });
                                                    rafPending.forEach(cb => origRAF(cb));
                                                    rafPending = [];
                                                    console.log('[PAGE-PATCH] Resumed ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                                    return cnt + timeoutCnt;
                                                };
                                                
                                                console.log('[PAGE-PATCH] Timer interception installed (addScriptToEvaluateOnNewDocument - First tool path)');
                                            })();
                                        \`
                                    });
                                    console.log('[CUSTOM-PATCH] addScriptToEvaluateOnNewDocument registered');
                                } catch (addScriptErr) {
                                    console.log('[CUSTOM-PATCH] addScriptToEvaluateOnNewDocument failed:', addScriptErr.message);
                                }
                                
                                // 在当前页面也注入 timer interception
                                const installResult = await pg.evaluate(() => {
                                    // 如果 timer patch 未安装，先安装
                                    if (!window.__CUSTOM_TIMER_PATCH__) {
                                        window.__CUSTOM_TIMER_PATCH__ = true;
                                        window.__CUSTOM_PAUSED__ = false;
                                        const origSetInterval = window.setInterval.bind(window);
                                        const origClearInterval = window.clearInterval.bind(window);
                                        const origSetTimeout = window.setTimeout.bind(window);
                                        const origClearTimeout = window.clearTimeout.bind(window);
                                        const origRAF = window.requestAnimationFrame.bind(window);
                                        const intervals = new Map();
                                        const timeouts = new Map();
                                        let rafPending = [];
                                        let idCounter = 900000;
                                        window.setInterval = function(fn, delay, ...args) {
                                            const id = idCounter++;
                                            if (!window.__CUSTOM_PAUSED__) {
                                                const realId = origSetInterval(fn, delay, ...args);
                                                intervals.set(id, { fn, delay, args, realId, type: 'active' });
                                            } else {
                                                intervals.set(id, { fn, delay, args, realId: null, type: 'paused' });
                                            }
                                            return id;
                                        };
                                        window.clearInterval = function(id) {
                                            const info = intervals.get(id);
                                            if (info && info.realId !== null) origClearInterval(info.realId);
                                            intervals.delete(id);
                                        };
                                        window.setTimeout = function(fn, delay, ...args) {
                                            const id = idCounter++;
                                            const startTime = Date.now();
                                            if (!window.__CUSTOM_PAUSED__) {
                                                const realId = origSetTimeout(() => { timeouts.delete(id); fn.apply(null, args); }, delay);
                                                timeouts.set(id, { fn, delay, args, realId, startTime, remaining: delay });
                                            } else {
                                                timeouts.set(id, { fn, delay, args, realId: null, startTime: null, remaining: delay });
                                            }
                                            return id;
                                        };
                                        window.clearTimeout = function(id) {
                                            const info = timeouts.get(id);
                                            if (info && info.realId !== null) origClearTimeout(info.realId);
                                            timeouts.delete(id);
                                        };
                                        window.requestAnimationFrame = function(cb) {
                                            if (window.__CUSTOM_PAUSED__) { rafPending.push(cb); return -1; }
                                            return origRAF(cb);
                                        };
                                        window.__pauseAllTimers__ = function() {
                                            window.__CUSTOM_PAUSED__ = true;
                                            let cnt = 0;
                                            intervals.forEach((info) => {
                                                if (info.realId !== null) { origClearInterval(info.realId); info.realId = null; info.type = 'paused'; cnt++; }
                                            });
                                            let timeoutCnt = 0;
                                            timeouts.forEach((info) => {
                                                if (info.realId !== null) { origClearTimeout(info.realId); info.remaining = Math.max(0, info.delay - (Date.now() - info.startTime)); info.realId = null; timeoutCnt++; }
                                            });
                                            console.log('[PAGE-PATCH] Paused ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                            return cnt + timeoutCnt;
                                        };
                                        window.__resumeAllTimers__ = function() {
                                            window.__CUSTOM_PAUSED__ = false;
                                            let cnt = 0;
                                            intervals.forEach((info) => {
                                                if (info.type === 'paused') { info.realId = origSetInterval(info.fn, info.delay, ...info.args); info.type = 'active'; cnt++; }
                                            });
                                            let timeoutCnt = 0;
                                            timeouts.forEach((info, id) => {
                                                if (info.realId === null && info.fn) { info.startTime = Date.now(); info.realId = origSetTimeout(() => { timeouts.delete(id); info.fn.apply(null, info.args || []); }, info.remaining || info.delay || 0); timeoutCnt++; }
                                            });
                                            rafPending.forEach(cb => origRAF(cb));
                                            rafPending = [];
                                            console.log('[PAGE-PATCH] Resumed ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                            return cnt + timeoutCnt;
                                        };
                                        console.log('[PAGE-PATCH] Timer interception RE-INSTALLED on new page (resume)');
                                    }
                                    
                                    // [SELECT-BADGE] 标记 select 元素并显示可见标签（当前页面）
                                    function markAllSelects() {
                                        document.querySelectorAll('select').forEach((sel, idx) => {
                                            if (!sel.dataset.agentSelectMarked) {
                                                const options = Array.from(sel.options).map(o => ({
                                                    value: o.value,
                                                    text: o.textContent.trim(),
                                                    selected: o.selected
                                                }));
                                                
                                                // 生成唯一选择器
                                                let selector = '';
                                                if (sel.id) {
                                                    selector = '#' + sel.id;
                                                } else if (sel.name) {
                                                    selector = 'select[name="' + sel.name + '"]';
                                                } else {
                                                    const uniqueId = 'agent_sel_' + idx + '_' + Math.random().toString(36).substr(2, 5);
                                                    sel.setAttribute('data-agent-id', uniqueId);
                                                    selector = 'select[data-agent-id="' + uniqueId + '"]';
                                                }
                                                
                                                sel.dataset.agentSelectMarked = 'true';
                                                sel.dataset.agentSelectOptions = JSON.stringify(options);
                                                sel.dataset.agentCurrentValue = sel.value;
                                                sel.dataset.agentSelector = selector;
                                                
                                                const badge = document.createElement('div');
                                                badge.className = '__agent_select_badge__';
                                                badge.style.cssText = 'position:fixed;background:rgba(255,140,0,0.95);color:white;padding:2px 6px;border-radius:3px;font-size:10px;font-family:monospace;z-index:999999;pointer-events:none;white-space:nowrap;max-width:350px;overflow:hidden;text-overflow:ellipsis;box-shadow:0 1px 3px rgba(0,0,0,0.3);';
                                                
                                                const currentText = sel.options[sel.selectedIndex]?.text || sel.value || '(empty)';
                                                const optionTexts = options.slice(0, 3).map(o => o.text).join(', ');
                                                const moreCount = options.length > 3 ? ' +' + (options.length - 3) + ' more' : '';
                                                badge.textContent = '🔽 [' + selector + '] ' + currentText + ' | ' + optionTexts + moreCount;
                                                badge.dataset.selectId = 'badge_' + Math.random().toString(36).substr(2, 9);
                                                sel.dataset.badgeId = badge.dataset.selectId;
                                                
                                                const rect = sel.getBoundingClientRect();
                                                badge.style.left = rect.left + 'px';
                                                badge.style.top = (rect.top - 18) + 'px';
                                                
                                                document.body.appendChild(badge);
                                                
                                                sel.addEventListener('change', function() {
                                                    this.dataset.agentCurrentValue = this.value;
                                                    const b = document.querySelector('[data-select-id="' + this.dataset.badgeId + '"]');
                                                    if (b) {
                                                        const newText = this.options[this.selectedIndex]?.text || this.value || '(empty)';
                                                        const opts = JSON.parse(this.dataset.agentSelectOptions || '[]');
                                                        const optTexts = opts.slice(0, 3).map(o => o.text).join(', ');
                                                        const more = opts.length > 3 ? ' +' + (opts.length - 3) + ' more' : '';
                                                        const selSelector = this.dataset.agentSelector || '';
                                                        b.textContent = '🔽 [' + selSelector + '] ' + newText + ' | ' + optTexts + more;
                                                    }
                                                });
                                            }
                                        });
                                        
                                        document.querySelectorAll('select[data-agent-select-marked]').forEach(sel => {
                                            const badge = document.querySelector('[data-select-id="' + sel.dataset.badgeId + '"]');
                                            if (badge) {
                                                const rect = sel.getBoundingClientRect();
                                                badge.style.left = rect.left + 'px';
                                                badge.style.top = (rect.top - 18) + 'px';
                                            }
                                        });
                                    }
                                    markAllSelects();
                                    console.log('[SELECT-BADGE] markAllSelects executed on current page');
                                    
                                    if (window.__resumeAllTimers__) {
                                        return window.__resumeAllTimers__();
                                    }
                                    return 'page ready';
                                });
                                console.log('[CUSTOM-PATCH] Timer install result:', installResult);
                            } catch (evalErr) {
                                console.log('[CUSTOM-PATCH] Resume timers failed:', evalErr.message);
                            }
                        } else {
                            console.log('[CUSTOM-PATCH] No page available for resume');
                        }
                        console.log('[CUSTOM-PATCH] Page resumed (Animation + Timers)');
                    } else {
                        // CDP client 已存在，执行 resume 操作
                        console.log('[CUSTOM-PATCH] Resuming page before tool execution...');
                        
                        // [FIX] 检测 Case 切换：如果页面是 about:blank，清理旧的 CDP client
                        let pg = null;
                        if (global.__CUSTOM_BROWSER_OPERATOR__) {
                            try { pg = await global.__CUSTOM_BROWSER_OPERATOR__.getActivePage(); } catch (e) {}
                        }
                        if (!pg) { pg = global.__CUSTOM_PAGE__; }
                        console.log('[CUSTOM-PATCH] Resume path: pg=' + (pg ? 'found' : 'null') + ', CDP=' + (global.__CUSTOM_CDP_CLIENT__ ? 'exists' : 'null'));
                        
                        let isCaseSwitch = false;
                        if (pg) {
                            try {
                                const currentUrl = await pg.url();
                                console.log('[CUSTOM-PATCH] Resume path: currentUrl=' + currentUrl);
                                if (currentUrl === 'about:blank' || currentUrl === 'chrome://newtab/') {
                                    console.log('[CUSTOM-PATCH] Detected case switch in resume path (page is ' + currentUrl + '), clearing stale CDP client...');
                                    isCaseSwitch = true;
                                    // 清理旧的 CDP client 和录屏器
                                    global.__CUSTOM_CDP_CLIENT__ = null;
                                    global.__CUSTOM_PAGE__ = null;
                                    if (global.__CUSTOM_SCREENCAST_RECORDER__) {
                                        try { await global.__CUSTOM_SCREENCAST_RECORDER__.stopRecording(); } catch (e) {}
                                        global.__CUSTOM_SCREENCAST_RECORDER__ = null;
                                    }
                                    console.log('[CUSTOM-PATCH] Stale CDP client cleared, will recreate on next Agent Loop Start');
                                }
                            } catch (urlErr) {
                                console.log('[CUSTOM-PATCH] Failed to check page URL:', urlErr.message);
                            }
                        }
                        
                        // 只有当页面不是 about:blank 时才执行 resume
                        if (!isCaseSwitch) {
                            try {
                                await global.__CUSTOM_CDP_CLIENT__.send('Animation.setPlaybackRate', { playbackRate: 1 });
                            } catch (animErr) {
                                console.log('[CUSTOM-PATCH] Animation.setPlaybackRate failed:', animErr.message);
                            }
                            
                            if (pg) {
                                try {
                                    const resumeResult = await pg.evaluate(() => {
                                        if (window.__resumeAllTimers__) {
                                            return window.__resumeAllTimers__();
                                        }
                                        return 'no __resumeAllTimers__';
                                    });
                                    console.log('[CUSTOM-PATCH] Resume timers result:', resumeResult);
                                } catch (e) {
                                    console.log('[CUSTOM-PATCH] Resume timers failed:', e.message);
                                }
                            }
                            console.log('[CUSTOM-PATCH] Page resumed (Animation + Timers)');
                        } else {
                            console.log('[CUSTOM-PATCH] Skip resume for case-switched blank page');
                        }
                    }
                }
            } catch (resumeErr) {
                console.log('[CUSTOM-PATCH] Resume error:', resumeErr.message);
            }
            
            // [SCREENCAST-PATCH] 开始录制（工具执行前）
            if (global.__CUSTOM_SCREENCAST_RECORDER__) {
                try {
                    const toolNameMatch = arguments[0]?.match?.(/name['"]*:\s*['"]([^'"]+)['"]/);
                    const toolName = toolNameMatch ? toolNameMatch[1] : 'unknown';
                    await global.__CUSTOM_SCREENCAST_RECORDER__.startRecording(toolName);
                } catch (recStartErr) {
                    console.log('[SCREENCAST-PATCH] Start recording failed:', recStartErr.message);
                }
            }
            $1\`[Tool] Executing:`
        );
        modifiedCount++;
        console.log('      ✅ 工具执行前恢复代码注入成功');
    } else {
        console.log('      ℹ️ 未找到或已注入');
    }

    // ============================================
    // 4. 工具执行后 - 等待 n ms 后暂停网页
    // ============================================
    console.log('   4. 工具执行后暂停网页...');

    const toolCompletedLog = /([\w.]+\.(?:debug|log|info)\s*\(\s*)`\[Tool\]\s*Execution completed:/;
    if (toolCompletedLog.test(content) && !content.includes('[CUSTOM-PATCH] pausePage after tool execution')) {
        content = content.replace(
            /([\w.]+\.(?:debug|log|info)\s*\(\s*)`\[Tool\]\s*Execution completed:/g,
            `// [CUSTOM-PATCH] pausePage after tool execution
            try {
                const pauseEnabled = process.env.AGENT_PAUSE_ENABLED !== 'false';
                const waitMs = parseInt(process.env.AGENT_PAUSE_WAIT_MS) || ${TOOL_WAIT_TIME};
                
                // [NAVIGATE-FIX] 检测是否是导航类工具（导航会刷新页面，跳过暂停）
                const _navigateTools = ['browser_navigate', 'browser_go_back', 'browser_go_forward', 'browser_refresh'];
                const _isNavigateTool = typeof toolName !== 'undefined' && 
                    _navigateTools.some(t => toolName.toLowerCase().includes(t.toLowerCase()));
                
                if (_isNavigateTool) {
                    console.log('[CUSTOM-PATCH] Skipping pause for navigation tool:', toolName);
                    console.log('[CUSTOM-PATCH] (Page will be re-initialized on next tool execution)');
                } else if (pauseEnabled) {
                    const existingClient = typeof global !== 'undefined' && global.__CUSTOM_CDP_CLIENT__;
                    if (existingClient) {
                        // 获取页面对象（提前，用于边等边截）
                        let pg = null;
                        if (typeof global !== 'undefined' && global.__CUSTOM_BROWSER_OPERATOR__) {
                            try { pg = await global.__CUSTOM_BROWSER_OPERATOR__.getActivePage(); } catch (e) {}
                        }
                        if (!pg && typeof global !== 'undefined') { pg = global.__CUSTOM_PAGE__; }

                        // 获取录屏策略: cdp_legacy (CDP+Legacy混合) 或 cdp_only (全CDP)
                        const recordStrategy = process.env.AGENT_RECORD_STRATEGY || 'cdp_legacy';
                        const recorder = typeof global !== 'undefined' && global.__CUSTOM_SCREENCAST_RECORDER__;

                        if (recordStrategy === 'cdp_legacy') {
                            // cdp_legacy 模式：等待期间停止 CDP，用 legacy 截图补充
                            // 先停止 CDP 录制，避免帧序列混乱
                            if (recorder && typeof recorder.stopRecording === 'function') {
                                try { await recorder.stopRecording(); } catch (e) {}
                            }

                            const screenshotFps = parseInt(process.env.AGENT_RECORD_FPS) || 10;
                            const frameCount = Math.max(1, Math.floor(waitMs / 1000 * screenshotFps));
                            const frameInterval = Math.floor(waitMs / frameCount);

                            console.log('[CUSTOM-PATCH] Waiting ' + waitMs + 'ms with ' + frameCount + ' legacy screenshots (CDP paused, strategy=' + recordStrategy + ')...');

                            for (let _fi = 0; _fi < frameCount; _fi++) {
                                // 主动截图添加到录屏帧序列
                                if (pg && recorder && typeof recorder.addFrameFromBuffer === 'function') {
                                    try {
                                        const _buf = await pg.screenshot({ type: 'jpeg', quality: 80 });
                                        recorder.addFrameFromBuffer(_buf, 'wait_' + _fi);
                                    } catch (_ssErr) {
                                        // 截图失败忽略（页面可能正在导航等）
                                    }
                                }
                                await new Promise(r => setTimeout(r, frameInterval));
                            }

                            // legacy 截图结束后，恢复 CDP 录制
                            if (recorder && typeof recorder.startRecording === 'function') {
                                try { await recorder.startRecording('resume_from_legacy'); } catch (e) {}
                            }
                        } else if (recordStrategy === 'cdp_only') {
                            // cdp_only 模式：等待期间继续用 CDP 被动推帧，不做 legacy 截图
                            // isRecording 保持 true，CDP 帧会继续被保存
                            console.log('[CUSTOM-PATCH] Waiting ' + waitMs + 'ms (CDP only mode, CDP continues recording)...');
                            await new Promise(r => setTimeout(r, waitMs));
                        } else {
                            // 默认或未知策略：等待
                            console.log('[CUSTOM-PATCH] Waiting ' + waitMs + 'ms...');
                            await new Promise(r => setTimeout(r, waitMs));
                        }

                        await existingClient.send('Animation.setPlaybackRate', { playbackRate: 0 });
                        // 调用页面注入的暂停函数（pg 已在上面获取）
                        if (pg) {
                            try {
                                const pauseResult = await pg.evaluate(() => {
                                    // 如果 timer patch 未安装，先安装
                                    if (!window.__CUSTOM_TIMER_PATCH__) {
                                        window.__CUSTOM_TIMER_PATCH__ = true;
                                        window.__CUSTOM_PAUSED__ = false;
                                        const origSetInterval = window.setInterval.bind(window);
                                        const origClearInterval = window.clearInterval.bind(window);
                                        const origSetTimeout = window.setTimeout.bind(window);
                                        const origClearTimeout = window.clearTimeout.bind(window);
                                        const origRAF = window.requestAnimationFrame.bind(window);
                                        const intervals = new Map();
                                        const timeouts = new Map();
                                        let rafPending = [];
                                        let idCounter = 900000;
                                        window.setInterval = function(fn, delay, ...args) {
                                            const id = idCounter++;
                                            if (!window.__CUSTOM_PAUSED__) {
                                                const realId = origSetInterval(fn, delay, ...args);
                                                intervals.set(id, { fn, delay, args, realId, type: 'active' });
                                            } else {
                                                intervals.set(id, { fn, delay, args, realId: null, type: 'paused' });
                                            }
                                            return id;
                                        };
                                        window.clearInterval = function(id) {
                                            const info = intervals.get(id);
                                            if (info && info.realId !== null) origClearInterval(info.realId);
                                            intervals.delete(id);
                                        };
                                        window.setTimeout = function(fn, delay, ...args) {
                                            const id = idCounter++;
                                            const startTime = Date.now();
                                            if (!window.__CUSTOM_PAUSED__) {
                                                const realId = origSetTimeout(() => { timeouts.delete(id); fn.apply(null, args); }, delay);
                                                timeouts.set(id, { fn, delay, args, realId, startTime, remaining: delay });
                                            } else {
                                                timeouts.set(id, { fn, delay, args, realId: null, startTime: null, remaining: delay });
                                            }
                                            return id;
                                        };
                                        window.clearTimeout = function(id) {
                                            const info = timeouts.get(id);
                                            if (info && info.realId !== null) origClearTimeout(info.realId);
                                            timeouts.delete(id);
                                        };
                                        window.requestAnimationFrame = function(cb) {
                                            if (window.__CUSTOM_PAUSED__) { rafPending.push(cb); return -1; }
                                            return origRAF(cb);
                                        };
                                        window.__pauseAllTimers__ = function() {
                                            window.__CUSTOM_PAUSED__ = true;
                                            let cnt = 0;
                                            intervals.forEach((info) => {
                                                if (info.realId !== null) { origClearInterval(info.realId); info.realId = null; info.type = 'paused'; cnt++; }
                                            });
                                            let timeoutCnt = 0;
                                            timeouts.forEach((info) => {
                                                if (info.realId !== null) { origClearTimeout(info.realId); info.remaining = Math.max(0, info.delay - (Date.now() - info.startTime)); info.realId = null; timeoutCnt++; }
                                            });
                                            console.log('[PAGE-PATCH] Paused ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                            return cnt + timeoutCnt;
                                        };
                                        window.__resumeAllTimers__ = function() {
                                            window.__CUSTOM_PAUSED__ = false;
                                            let cnt = 0;
                                            intervals.forEach((info) => {
                                                if (info.type === 'paused') { info.realId = origSetInterval(info.fn, info.delay, ...info.args); info.type = 'active'; cnt++; }
                                            });
                                            let timeoutCnt = 0;
                                            timeouts.forEach((info, id) => {
                                                if (info.realId === null && info.fn) { info.startTime = Date.now(); info.realId = origSetTimeout(() => { timeouts.delete(id); info.fn.apply(null, info.args || []); }, info.remaining || info.delay || 0); timeoutCnt++; }
                                            });
                                            rafPending.forEach(cb => origRAF(cb));
                                            rafPending = [];
                                            console.log('[PAGE-PATCH] Resumed ' + cnt + ' intervals, ' + timeoutCnt + ' timeouts');
                                            return cnt + timeoutCnt;
                                        };
                                        console.log('[PAGE-PATCH] Timer interception RE-INSTALLED on new page');
                                    }
                                    
                                    if (!window.__pauseAllTimers__) return { status: 'FUNC_MISSING', paused: false };
                                    
                                    // 直接调用注入的暂停函数
                                    const cnt = window.__pauseAllTimers__();
                                    return { 
                                        status: 'PAUSED', 
                                        paused: window.__CUSTOM_PAUSED__,
                                        timers: cnt
                                    };
                                });
                                console.log('[CUSTOM-PATCH] Pause result:', JSON.stringify(pauseResult));
                            } catch (evalErr) {
                                console.log('[CUSTOM-PATCH] Pause timers failed:', evalErr.message);
                            }
                        } else {
                            console.log('[CUSTOM-PATCH] No page available for pause');
                        }
                        console.log('[CUSTOM-PATCH] Page paused after tool (Animation + Timers)');
                    }
                }
            } catch (pauseErr) {
                console.log('[CUSTOM-PATCH] Pause error:', pauseErr.message);
            }
            
            // [SCREENCAST-PATCH] 停止录制（工具执行后）
            if (global.__CUSTOM_SCREENCAST_RECORDER__) {
                try {
                    // [NAVIGATE-FIX] 如果是导航工具，跳过 stopRecording 或者加超时
                    const _navTools = ['browser_navigate', 'browser_go_back', 'browser_go_forward', 'browser_refresh'];
                    const _isNav = typeof toolName !== 'undefined' && 
                        _navTools.some(t => toolName.toLowerCase().includes(t.toLowerCase()));
                    
                    if (_isNav) {
                        console.log('[SCREENCAST-PATCH] Skipping stopRecording wait for navigation tool');
                        // 触发 stop 但不 await，防止页面卸载导致挂起
                        global.__CUSTOM_SCREENCAST_RECORDER__.stopRecording().catch(e => {
                            console.log('[SCREENCAST-PATCH] Background stopRecording error:', e.message);
                        });
                    } else {
                        // 添加超时保护，防止录屏挂起阻塞整个 Agent
                        await Promise.race([
                            global.__CUSTOM_SCREENCAST_RECORDER__.stopRecording(),
                            new Promise((_, reject) => setTimeout(() => reject(new Error('stopRecording timeout (10s)')), 10000))
                        ]);
                    }
                } catch (recStopErr) {
                    console.log('[SCREENCAST-PATCH] Stop recording failed or timeout:', recStopErr.message);
                }
            }
            $1\`[Tool] Execution completed:`
        );
        modifiedCount++;
        console.log('      ✅ 工具执行后暂停代码注入成功');
    } else {
        console.log('      ℹ️ 未找到或已注入');
    }

    // ============================================
    // 4.5 修改 browser_select - 支持文本匹配
    // ============================================
    console.log('   4.5 修改 browser_select (支持文本匹配)...');

    if (!hasBrowserSelectPatch) {
        // 原代码: await page.select(args.selector, args.value);
        const selectPattern = /await page\.select\(args\.selector,\s*args\.value\);/g;
        if (selectPattern.test(content)) {
            content = content.replace(
                /await page\.select\(args\.selector,\s*args\.value\);/g,
                `// [CUSTOM-SELECT-PATCH] Support text matching for select options
                            // [CUSTOM-SELECT-PATCH] Auto-fix: remove square brackets if model included them
                            let fixedSelector = args.selector;
                            if (fixedSelector.startsWith('[') && fixedSelector.includes(']')) {
                                const match = fixedSelector.match(/^\\[([^\\]]+)\\]/);
                                if (match) {
                                    fixedSelector = match[1];
                                    console.log('[CUSTOM-SELECT-PATCH] Auto-fixed selector:', args.selector, '->', fixedSelector);
                                }
                            }
                            
                            const selectResult = await page.evaluate((sel, val) => {
                                const selectEl = document.querySelector(sel);
                                if (!selectEl) return { success: false, error: 'Element not found' };
                                
                                // 1. 先尝试直接 value 匹配
                                for (let i = 0; i < selectEl.options.length; i++) {
                                    if (selectEl.options[i].value === val) {
                                        selectEl.selectedIndex = i;
                                        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                                        return { success: true, matchedBy: 'value', selectedValue: val };
                                    }
                                }
                                
                                // 2. 再尝试文本匹配（精确）
                                for (let i = 0; i < selectEl.options.length; i++) {
                                    if (selectEl.options[i].text.trim() === val.trim()) {
                                        selectEl.selectedIndex = i;
                                        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                                        return { success: true, matchedBy: 'exactText', selectedValue: selectEl.options[i].value };
                                    }
                                }
                                
                                // 3. 再尝试文本匹配（包含）
                                for (let i = 0; i < selectEl.options.length; i++) {
                                    if (selectEl.options[i].text.trim().includes(val.trim()) || val.trim().includes(selectEl.options[i].text.trim())) {
                                        selectEl.selectedIndex = i;
                                        selectEl.dispatchEvent(new Event('change', { bubbles: true }));
                                        return { success: true, matchedBy: 'partialText', selectedValue: selectEl.options[i].value };
                                    }
                                }
                                
                                return { success: false, error: 'No matching option found', availableOptions: Array.from(selectEl.options).map(o => ({ value: o.value, text: o.text })).slice(0, 10) };
                            }, fixedSelector, args.value);
                            
                            if (!selectResult.success) {
                                console.log('[CUSTOM-SELECT-PATCH] Select failed:', JSON.stringify(selectResult));
                                throw new Error(selectResult.error + (selectResult.availableOptions ? ' Available: ' + JSON.stringify(selectResult.availableOptions) : ''));
                            }
                            console.log('[CUSTOM-SELECT-PATCH] Select succeeded:', JSON.stringify(selectResult));`
            );
            modifiedCount++;
            console.log('      ✅ browser_select 文本匹配修改成功');
        } else {
            console.log('      ℹ️ 未找到 browser_select 匹配模式');
        }
    } else {
        console.log('      ⚠️ 已修改过');
    }

    // ============================================
    // 5. 修改 System Prompt - 添加 select 和 dialog 处理说明
    // ============================================
    console.log('   5. 修改 System Prompt (添加 select 和 dialog 说明)...');

    const hasSelectDialogPrompt = content.includes('<agent_injected_badges>');

    if (!hasSelectDialogPrompt) {
        // 查找 </browser_rules> 标记，在它前面插入新内容
        const browserRulesEndPattern = /<\/browser_rules>`/g;
        if (browserRulesEndPattern.test(content)) {
            content = content.replace(
                /<\/browser_rules>`/g,
                `
<agent_injected_badges>
⚠️ IMPORTANT: The following visual badges are INJECTED BY THE AGENT SYSTEM (not part of the original website):

1. 🔽 Orange badges - Mark <select> dropdown elements → MUST use browser_select tool
2. 📅 Purple badges - Mark date/time input elements → MUST use browser_form_input_fill tool  
3. 🔔 Red badges - Browser dialogs (alert/confirm/prompt) have been AUTO-HANDLED by the system. Badge shows: dialog type, message content, and the default action taken (usually "accepted"). No action needed from you.

WHEN YOU SEE ORANGE/PURPLE BADGES:
- The badge contains the EXACT CSS selector you must use
- Copy the selector exactly from the badge brackets, e.g. "#country" or "select[data-agent-id=\\"..\\"]"
- Normal text input fields (without badges) can be handled normally with browser_form_input_fill or browser_type
</agent_injected_badges>

<select_element_handling>
⚠️ CRITICAL: HTML <select> dropdown elements require MANDATORY special handling.

🚫 ABSOLUTE PROHIBITION:
- NEVER click on any element with an orange "🔽" badge using browser_vision_control or any click tool
- NEVER try to open dropdown menus by clicking - the OS-level menu is INVISIBLE in screenshots
- Clicking on select elements WILL FAIL and waste steps

✅ MANDATORY ACTION:
- When you see an orange badge starting with "🔽 [selector]", you MUST use browser_select tool
- The badge shows the CSS selector in brackets - USE THIS EXACT SELECTOR

BADGE FORMAT:
- Format: "🔽 [CSS_SELECTOR] current_value | option1, option2, option3 +N more"
- Example: "🔽 [#country] USA | USA, Canada, UK +5 more"
- Example: "🔽 [select[data-agent-id=\\"agent_sel_0_abc12\\"]] Select time | 09:00, 11:00, 14:00 +2 more"

CORRECT WORKFLOW:
1. See orange badge → Extract the selector from brackets (e.g., "#country" or "select[data-agent-id=\\"...\"]")
2. Call browser_select with that exact selector and desired value
3. Example: browser_select(selector="#country", value="Canada")
4. Example: browser_select(selector="select[data-agent-id=\\"agent_sel_0_abc12\\"]", value="09:00 AM - 11:00 AM")

COMMON MISTAKES TO AVOID:
- ❌ Using browser_vision_control click on a select element
- ❌ Guessing selector names like "select[name=\\"lessonTime\\"]" without seeing them in badge
- ❌ Using index "0" instead of actual CSS selector
- ✅ Always copy the exact selector shown in the orange badge brackets
</select_element_handling>

<date_input_handling>
⚠️ CRITICAL: HTML date/time input elements (type="date", "time", "datetime-local", "month", "week") require MANDATORY special handling.

🚫 ABSOLUTE PROHIBITION:
- NEVER click on any element with a purple "📅" badge using browser_vision_control or any click tool
- NEVER try to open the date picker by clicking - the OS-level calendar is INVISIBLE in screenshots
- Clicking on date inputs WILL FAIL and waste steps

✅ MANDATORY ACTION:
- When you see a purple badge starting with "📅 [selector]", you MUST use browser_form_input_fill tool
- The badge shows the CSS selector in brackets - USE THIS EXACT SELECTOR
- The badge also shows the required format (e.g., "YYYY-MM-DD" for date inputs)

BADGE FORMAT:
- Format: "📅 [CSS_SELECTOR] current_value | Format: FORMAT_HINT"
- Example: "📅 [#lessonDate] (empty) | Format: YYYY-MM-DD"
- Example: "📅 [input[name=\\"appointmentTime\\"]] 14:30 | Format: HH:MM"

DATE/TIME FORMATS (IMPORTANT - use exactly these formats):
- date: "YYYY-MM-DD" (e.g., "2025-03-15")
- time: "HH:MM" (e.g., "14:30")
- datetime-local: "YYYY-MM-DDTHH:MM" (e.g., "2025-03-15T14:30")
- month: "YYYY-MM" (e.g., "2025-03")
- week: "YYYY-Www" (e.g., "2025-W12")

CORRECT WORKFLOW:
1. See purple badge → Extract the selector from brackets (e.g., "#lessonDate")
2. Determine the correct format from the badge (e.g., "YYYY-MM-DD")
3. Call browser_form_input_fill with selector and properly formatted value
4. Example: browser_form_input_fill(selector="#lessonDate", value="2025-03-20")
5. Example: browser_form_input_fill(selector="input[name=\\"startTime\\"]", value="09:00")

COMMON MISTAKES TO AVOID:
- ❌ Clicking on date inputs expecting a calendar popup
- ❌ Using wrong date format (e.g., "03/20/2025" instead of "2025-03-20")
- ❌ Using browser_select instead of browser_form_input_fill for date inputs
- ✅ Always use the exact format shown in the badge (YYYY-MM-DD, HH:MM, etc.)
</date_input_handling>

<dialog_handling>
Browser dialogs (alert/confirm/prompt/beforeunload) are automatically handled by the system:

WHAT HAPPENS:
- When a JavaScript dialog appears, the system immediately handles it
- Default behavior:
  * alert: Automatically accepted
  * confirm: Automatically accepted (configurable via AGENT_DIALOG_CONFIRM_DEFAULT env var)
  * prompt: Accepted with default value or empty string
  * beforeunload: Automatically accepted

VISUAL INDICATOR:
- After a dialog is handled, a red badge appears in the bottom-right corner of the page
- The badge shows: Dialog type, action taken, and message preview
- Badge format: "🔔 Dialog Handled | Type: confirm | Action: accepted | Message: '...'"
- The badge is removed before the next tool execution

WHAT YOU SHOULD DO:
- When you see the dialog badge in a screenshot, acknowledge it in your reasoning
- The dialog has already been handled automatically
- Continue with your task based on the action taken (usually "accepted")
- If the dialog suggests a potential issue (e.g., "Are you sure you want to delete?"), consider whether your action was appropriate

WHY THIS MATTERS:
- Browser dialogs block JavaScript execution and cannot be captured in screenshots
- Manual handling would cause the agent to hang indefinitely
- Automatic handling ensures smooth operation while keeping you informed
</dialog_handling>
</browser_rules>\``
            );
            modifiedCount++;
            console.log('      ✅ System Prompt select/dialog 说明注入成功');
        } else {
            console.log('      ⚠️ 未找到 </browser_rules> 标记');
        }
    } else {
        console.log('      ⚠️ 已注入过');
    }

    // ============================================
    // 4.5 修复 sessionId 传递 (browserGUIAgent.onEachAgentLoopStart)
    // ============================================
    const hasSessionIdFix = content.includes('onEachAgentLoopStart(eventStream, isReplaySnapshot, sessionId)');
    if (!hasSessionIdFix) {
        // Fix caller: pass sessionId through to browserGUIAgent
        const callerPattern = /await this\.browserGUIAgent\.onEachAgentLoopStart\(eventStream, isReplaySnapshot\);/g;
        if (callerPattern.test(content)) {
            content = content.replace(
                /await this\.browserGUIAgent\.onEachAgentLoopStart\(eventStream, isReplaySnapshot\);/g,
                'await this.browserGUIAgent.onEachAgentLoopStart(eventStream, isReplaySnapshot, sessionId);'
            );
            console.log('      ✅ sessionId 传递修复成功 (caller)');
        }
        // Fix signature: add sessionId parameter
        const sigPattern = /async onEachAgentLoopStart\(eventStream, isReplaySnapshot = false\)\s*\{/g;
        if (sigPattern.test(content)) {
            content = content.replace(
                /async onEachAgentLoopStart\(eventStream, isReplaySnapshot = false\)\s*\{/g,
                'async onEachAgentLoopStart(eventStream, isReplaySnapshot = false, sessionId) {'
            );
            console.log('      ✅ sessionId 传递修复成功 (signature)');
        }
    } else {
        console.log('      ⚠️ sessionId 传递已修复');
    }

    // 写入文件
    fs.writeFileSync(DIST_FILE, content, 'utf-8');

    // 验证
    const verifyContent = fs.readFileSync(DIST_FILE, 'utf-8');
    const verifyPress = verifyContent.includes('[CUSTOM] browser_press_key:');
    const verifyKeyEnum = verifyContent.includes('[CUSTOM-KEY-ENUM]');
    const verifyPause = verifyContent.includes('[CUSTOM-PATCH]');
    const verifySelectDialog = verifyContent.includes('<agent_injected_badges>');

    if (verifyPress || verifyKeyEnum || verifyPause || verifySelectDialog) {
        console.log('   ✅ 验证通过');
        totalModified++;
    } else {
        console.log('   ❌ 验证失败');
    }
}

// ============================================
// 5. 修改 @tarko/agent 工具执行器（支持 DOM 模式暂停/恢复）
// ============================================
console.log('\n正在查找 @tarko/agent 包...');

const tarkoAgentPaths = [];

// 查找 @tarko/agent 的 tool-manager.mjs 和 tool-processor.mjs
try {
    const nodeModulesBase = path.join(__dirname, 'node_modules/.pnpm');
    if (fs.existsSync(nodeModulesBase)) {
        const dirs = fs.readdirSync(nodeModulesBase).filter(d => d.startsWith('@tarko+agent@'));
        for (const dir of dirs) {
            const toolManagerPath = path.join(nodeModulesBase, dir, 'node_modules/@tarko/agent/dist/agent/tool-manager.mjs');
            const toolProcessorPath = path.join(nodeModulesBase, dir, 'node_modules/@tarko/agent/dist/agent/runner/tool-processor.mjs');
            if (fs.existsSync(toolManagerPath)) tarkoAgentPaths.push(toolManagerPath);
            if (fs.existsSync(toolProcessorPath)) tarkoAgentPaths.push(toolProcessorPath);
        }
    }
} catch (e) {
    console.log('搜索 @tarko/agent 时出错:', e.message);
}

console.log(`找到 ${tarkoAgentPaths.length} 个 @tarko/agent 工具执行文件`);

let tarkoModified = 0;

for (const TARKO_FILE of tarkoAgentPaths) {
    console.log(`\n正在处理: ...${TARKO_FILE.substring(TARKO_FILE.length - 60)}`);

    let content = fs.readFileSync(TARKO_FILE, 'utf-8');

    // 检测是否已修改
    if (content.includes('[CUSTOM-PATCH-TARKO]')) {
        console.log('   ⚠️ 已修改过，跳过');
        tarkoModified++;
        continue;
    }

    // 备份
    const backupFile = TARKO_FILE + '.backup';
    if (!fs.existsSync(backupFile)) {
        fs.copyFileSync(TARKO_FILE, backupFile);
        console.log(`   备份到: ...${backupFile.substring(backupFile.length - 40)}`);
    }

    let modified = false;

    // 修改 [Tool] Executing - 工具执行前恢复网页
    const execPattern = /this\.logger\.info\(`\[Tool\] Executing: "\$\{toolName\}"/g;
    if (execPattern.test(content)) {
        content = content.replace(
            /this\.logger\.info\(`\[Tool\] Executing: "\$\{toolName\}"/g,
            `// [CUSTOM-PATCH-TARKO] DOM mode: create CDP client on first tool, then resume
            try {
                const pauseEnabled = typeof process !== 'undefined' && process.env.AGENT_PAUSE_ENABLED !== 'false';
                if (pauseEnabled && typeof global !== 'undefined') {
                    // DOM 模式下首次执行时创建 CDP client
                    if (!global.__CUSTOM_CDP_CLIENT__ && this.browserContext) {
                        console.log('[CUSTOM-PATCH-TARKO] DOM mode first tool - creating CDP client...');
                        try {
                            const pages = this.browserContext.pages ? await this.browserContext.pages() : [];
                            const page = pages.length > 0 ? pages[pages.length - 1] : null;
                            if (page) {
                                const client = await page.target().createCDPSession();
                                await client.send('Animation.enable');
                                global.__CUSTOM_CDP_CLIENT__ = client;
                                global.__CUSTOM_TARKO_PAGE__ = page;
                                console.log('[CUSTOM-PATCH-TARKO] CDP client created for DOM mode');
                                
                                // 注入 timer interception
                                await client.send('Page.enable');
                                await page.evaluate(() => {
                                    if (window.__CUSTOM_TIMER_PATCH__) return 'already installed';
                                    window.__CUSTOM_TIMER_PATCH__ = true;
                                    window.__CUSTOM_PAUSED__ = false;
                                    const origSetInterval = window.setInterval.bind(window);
                                    const origClearInterval = window.clearInterval.bind(window);
                                    const origSetTimeout = window.setTimeout.bind(window);
                                    const origClearTimeout = window.clearTimeout.bind(window);
                                    const origRAF = window.requestAnimationFrame.bind(window);
                                    const intervals = new Map();
                                    const timeouts = new Map();
                                    let rafPending = [];
                                    let idCounter = 900000;
                                    window.setInterval = function(fn, delay, ...args) {
                                        const id = idCounter++;
                                        if (!window.__CUSTOM_PAUSED__) {
                                            const realId = origSetInterval(fn, delay, ...args);
                                            intervals.set(id, { fn, delay, args, realId, type: 'active' });
                                        } else {
                                            intervals.set(id, { fn, delay, args, realId: null, type: 'paused' });
                                        }
                                        return id;
                                    };
                                    window.clearInterval = function(id) {
                                        const info = intervals.get(id);
                                        if (info && info.realId !== null) origClearInterval(info.realId);
                                        intervals.delete(id);
                                    };
                                    window.setTimeout = function(fn, delay, ...args) {
                                        const id = idCounter++;
                                        const startTime = Date.now();
                                        if (!window.__CUSTOM_PAUSED__) {
                                            const realId = origSetTimeout(() => { timeouts.delete(id); fn.apply(null, args); }, delay);
                                            timeouts.set(id, { fn, delay, args, realId, startTime, remaining: delay });
                                        } else {
                                            timeouts.set(id, { fn, delay, args, realId: null, startTime: null, remaining: delay });
                                        }
                                        return id;
                                    };
                                    window.clearTimeout = function(id) {
                                        const info = timeouts.get(id);
                                        if (info && info.realId !== null) origClearTimeout(info.realId);
                                        timeouts.delete(id);
                                    };
                                    window.requestAnimationFrame = function(cb) {
                                        if (window.__CUSTOM_PAUSED__) { rafPending.push(cb); return -1; }
                                        return origRAF(cb);
                                    };
                                    window.__pauseAllTimers__ = function() {
                                        window.__CUSTOM_PAUSED__ = true;
                                        intervals.forEach((info) => {
                                            if (info.realId !== null) { origClearInterval(info.realId); info.realId = null; info.type = 'paused'; }
                                        });
                                        timeouts.forEach((info) => {
                                            if (info.realId !== null) { origClearTimeout(info.realId); info.remaining = Math.max(0, info.delay - (Date.now() - info.startTime)); info.realId = null; }
                                        });
                                    };
                                    window.__resumeAllTimers__ = function() {
                                        window.__CUSTOM_PAUSED__ = false;
                                        intervals.forEach((info) => {
                                            if (info.type === 'paused') { info.realId = origSetInterval(info.fn, info.delay, ...info.args); info.type = 'active'; }
                                        });
                                        timeouts.forEach((info, id) => {
                                            if (info.realId === null && info.fn) { info.startTime = Date.now(); info.realId = origSetTimeout(() => { timeouts.delete(id); info.fn.apply(null, info.args || []); }, info.remaining || info.delay || 0); }
                                        });
                                        rafPending.forEach(cb => origRAF(cb));
                                        rafPending = [];
                                    };
                                    console.log('[PAGE-PATCH-TARKO] Timer interception installed');
                                    return 'installed';
                                });
                            }
                        } catch (e) { console.log('[CUSTOM-PATCH-TARKO] CDP client creation failed:', e.message); }
                    }
                    // 恢复网页
                    if (global.__CUSTOM_CDP_CLIENT__) {
                        console.log('[CUSTOM-PATCH-TARKO] Resuming page before tool (DOM mode)...');
                        await global.__CUSTOM_CDP_CLIENT__.send('Animation.setPlaybackRate', { playbackRate: 1 });
                        if (global.__CUSTOM_TARKO_PAGE__) {
                            try { await global.__CUSTOM_TARKO_PAGE__.evaluate(() => window.__resumeAllTimers__ && window.__resumeAllTimers__()); } catch (e) {}
                        }
                        console.log('[CUSTOM-PATCH-TARKO] Page resumed');
                    }
                }
            } catch (e) { console.log('[CUSTOM-PATCH-TARKO] Resume error:', e.message); }
            this.logger.info(\`[Tool] Executing: "\${toolName}"`
        );
        modified = true;
        console.log('   ✅ [Tool] Executing 恢复代码注入成功');
    }

    // 修改 [Tool] Execution completed - 工具执行后暂停网页
    const completedPattern = /this\.logger\.info\(\s*`\[Tool\] Execution completed: "\$\{toolName\}"/g;
    if (completedPattern.test(content)) {
        content = content.replace(
            /this\.logger\.info\(\s*`\[Tool\] Execution completed: "\$\{toolName\}"/g,
            `// [CUSTOM-PATCH-TARKO] pausePage after tool execution (DOM mode)
            try {
                const pauseEnabled = typeof process !== 'undefined' && process.env.AGENT_PAUSE_ENABLED !== 'false';
                const waitMs = parseInt(process.env.AGENT_PAUSE_WAIT_MS) || ${TOOL_WAIT_TIME};
                if (pauseEnabled && typeof global !== 'undefined' && global.__CUSTOM_CDP_CLIENT__) {
                    // 获取页面对象（用于边等边截）
                    let _tarkoPg = null;
                    if (global.__CUSTOM_BROWSER_OPERATOR__) {
                        try { _tarkoPg = await global.__CUSTOM_BROWSER_OPERATOR__.getActivePage(); } catch (e) {}
                    }
                    if (!_tarkoPg) { _tarkoPg = global.__CUSTOM_TARKO_PAGE__; }

                    // 获取录屏策略: cdp_legacy (CDP+Legacy混合) 或 cdp_only (全CDP)
                    const _recordStrategy = typeof process !== 'undefined' && process.env.AGENT_RECORD_STRATEGY || 'cdp_legacy';
                    const _recorder = global.__CUSTOM_SCREENCAST_RECORDER__;

                    if (_recordStrategy === 'cdp_legacy') {
                        // cdp_legacy 模式：等待期间停止 CDP，用 legacy 截图补充
                        // 先停止 CDP 录制，避免帧序列混乱
                        if (_recorder && typeof _recorder.stopRecording === 'function') {
                            try { await _recorder.stopRecording(); } catch (e) {}
                        }

                        const _screenshotFps = parseInt(process.env.AGENT_RECORD_FPS) || 10;
                        const _frameCount = Math.max(1, Math.floor(waitMs / 1000 * _screenshotFps));
                        const _frameInterval = Math.floor(waitMs / _frameCount);

                        console.log('[CUSTOM-PATCH-TARKO] Waiting ' + waitMs + 'ms with ' + _frameCount + ' legacy screenshots (CDP paused, DOM mode, strategy=' + _recordStrategy + ')...');

                        for (let _fi = 0; _fi < _frameCount; _fi++) {
                            if (_tarkoPg && _recorder && typeof _recorder.addFrameFromBuffer === 'function') {
                                try {
                                    const _buf = await _tarkoPg.screenshot({ type: 'jpeg', quality: 80 });
                                    _recorder.addFrameFromBuffer(_buf, 'wait_tarko_' + _fi);
                                } catch (_ssErr) {}
                            }
                            await new Promise(r => setTimeout(r, _frameInterval));
                        }

                        // legacy 截图结束后，恢复 CDP 录制
                        if (_recorder && typeof _recorder.startRecording === 'function') {
                            try { await _recorder.startRecording('resume_from_legacy_tarko'); } catch (e) {}
                        }
                    } else if (_recordStrategy === 'cdp_only') {
                        // cdp_only 模式：等待期间继续用 CDP 被动推帧，不做 legacy 截图
                        // isRecording 保持 true，CDP 帧会继续被保存
                        console.log('[CUSTOM-PATCH-TARKO] Waiting ' + waitMs + 'ms (CDP only mode, CDP continues recording, DOM mode)...');
                        await new Promise(r => setTimeout(r, waitMs));
                    } else {
                        // 默认或未知策略：等待
                        console.log('[CUSTOM-PATCH-TARKO] Waiting ' + waitMs + 'ms (DOM mode)...');
                        await new Promise(r => setTimeout(r, waitMs));
                    }

                    await global.__CUSTOM_CDP_CLIENT__.send('Animation.setPlaybackRate', { playbackRate: 0 });

                    // 暂停 timers
                    if (_tarkoPg) {
                        try
 {
                            await _tarkoPg.evaluate(() => window.__pauseAllTimers__ && window.__pauseAllTimers__());
                        } catch (e) { console.log('[CUSTOM-PATCH-TARKO] Pause timers failed:', e.message); }
                    }
                    console.log('[CUSTOM-PATCH-TARKO] Page paused after tool (DOM mode)');
                }
            } catch (e) { console.log('[CUSTOM-PATCH-TARKO] Pause error:', e.message); }
            this.logger.info(\`[Tool] Execution completed: "\${toolName}"`
        );
        modified = true;
        console.log('   ✅ [Tool] Execution completed 暂停代码注入成功');
    }

    if (modified) {
        fs.writeFileSync(TARKO_FILE, content, 'utf-8');
        tarkoModified++;
        console.log('   ✅ 文件已保存');
    } else {
        console.log('   ℹ️ 未找到匹配模式');
    }
}

console.log('\n======================================');
console.log('[CUSTOM PATCH] 完成!');
console.log(`@agent-tars/core: 成功修改 ${totalModified}/${possiblePaths.length} 个文件`);
console.log(`@tarko/agent: 成功修改 ${tarkoModified}/${tarkoAgentPaths.length} 个文件`);
console.log('======================================\n');

// ============================================
// 6. 修改 @agent-infra/mcp-server-browser (修复 Hybrid 模式 browser_click)
// ============================================
console.log('\n正在查找 @agent-infra/mcp-server-browser 包...');

const mcpBrowserPaths = [];
try {
    const nodeModulesBase = path.join(__dirname, 'node_modules/.pnpm');
    if (fs.existsSync(nodeModulesBase)) {
        const dirs = fs.readdirSync(nodeModulesBase).filter(d => d.startsWith('@agent-infra+mcp-server-browser@'));
        for (const dir of dirs) {
            const serverPath = path.join(nodeModulesBase, dir, 'node_modules/@agent-infra/mcp-server-browser/dist/server.js');
            if (fs.existsSync(serverPath)) {
                mcpBrowserPaths.push(serverPath);
            }
        }
    }
} catch (e) {
    console.log('搜索 mcp-server-browser 时出错:', e.message);
}

console.log(`找到 ${mcpBrowserPaths.length} 个 @agent-infra/mcp-server-browser 文件`);

let mcpModified = 0;

for (const MCP_FILE of mcpBrowserPaths) {
    console.log(`\n正在处理: ...${MCP_FILE.substring(MCP_FILE.length - 70)}`);

    let content = fs.readFileSync(MCP_FILE, 'utf-8');

    // 检测是否已修改
    if (content.includes('[CUSTOM-MCP-FIX]')) {
        console.log('   ⚠️ 已修改过，跳过');
        mcpModified++;
        continue;
    }

    // 备份
    const backupFile = MCP_FILE + '.backup';
    if (!fs.existsSync(backupFile)) {
        fs.copyFileSync(MCP_FILE, backupFile);
        console.log(`   备份到: ...${backupFile.substring(backupFile.length - 50)}`);
    }

    let modified = false;

    // 修复 browser_click: 自动重建 selectorMap 并检测 element 是否为 null
    // 原代码：
    //   const elementNode = store.selectorMap?.get(Number(args?.index));
    //   element = await locateElement(page, elementNode);
    //   await Promise.race([element?.click(), ...]);
    // 问题：当 element 为 null 时，element?.click() 返回 undefined，Promise.race 立即 resolve，导致假成功

    const clickPattern = /browser_click:\s*async\s*\(args\)\s*=>\s*\{\s*try\s*\{\s*let\s+element\s*=\s*null;/g;
    if (clickPattern.test(content)) {
        content = content.replace(
            /browser_click:\s*async\s*\(args\)\s*=>\s*\{\s*try\s*\{\s*let\s+element\s*=\s*null;/g,
            `browser_click: async (args)=>{
            // [CUSTOM-MCP-FIX] Auto-rebuild selectorMap if missing
            try {
                let element = null;
                const argIndex = Number(args?.index);
                
                // 检查 selectorMap 是否为空或不包含目标索引
                if (!store.selectorMap || !store.selectorMap.has(argIndex)) {
                    console.log('[CUSTOM-MCP-FIX] browser_click: selectorMap missing index', argIndex, ', rebuilding DOM tree...');
                    try {
                        await buildDomTree(page);
                        console.log('[CUSTOM-MCP-FIX] DOM tree rebuilt, selectorMap size:', store.selectorMap ? store.selectorMap.size : 0);
                    } catch (rebuildErr) {
                        console.log('[CUSTOM-MCP-FIX] DOM tree rebuild failed:', rebuildErr.message);
                    }
                }`
        );
        modified = true;
        console.log('   ✅ browser_click 自动重建 selectorMap 注入成功');
    }

    // 修复 Promise.race 假成功问题
    // 原代码：await Promise.race([null == element ? void 0 : element.click(), ...])
    // 修复：先检查 element 是否为 null
    const racePattern = /await\s+Promise\.race\(\[\s*null\s*==\s*element\s*\?\s*void\s*0\s*:\s*element\.click\(\)/g;
    if (racePattern.test(content)) {
        content = content.replace(
            /await\s+Promise\.race\(\[\s*null\s*==\s*element\s*\?\s*void\s*0\s*:\s*element\.click\(\)/g,
            `// [CUSTOM-MCP-FIX] Check element before click to avoid false success
                    if (!element) {
                        console.log('[CUSTOM-MCP-FIX] browser_click: element is null after locate, returning error');
                        return {
                            content: [{ type: 'text', text: 'Element with index ' + args?.index + ' not found (element is null after locateElement)' }],
                            isError: true
                        };
                    }
                    await Promise.race([element.click()`
        );
        modified = true;
        console.log('   ✅ browser_click null 检查注入成功');
    }

    // 优化默认等待时间配置（减少游戏场景下的延迟）
    const configPattern = /const DEFAULT_BROWSER_CONTEXT_CONFIG = \{\s*minimumWaitPageLoadTime:\s*1,\s*waitForNetworkIdlePageLoadTime:\s*1\.0,\s*maximumWaitPageLoadTime:\s*5\.0,\s*waitBetweenActions:\s*1\.0,/g;
    if (configPattern.test(content)) {
        content = content.replace(
            /const DEFAULT_BROWSER_CONTEXT_CONFIG = \{\s*minimumWaitPageLoadTime:\s*1,\s*waitForNetworkIdlePageLoadTime:\s*1\.0,\s*maximumWaitPageLoadTime:\s*5\.0,\s*waitBetweenActions:\s*1\.0,/g,
            `const DEFAULT_BROWSER_CONTEXT_CONFIG = {
    // [CUSTOM-MCP-FIX] Reduced wait times for faster interaction (especially for games)
    minimumWaitPageLoadTime: 0.1,
    waitForNetworkIdlePageLoadTime: 0.5,
    maximumWaitPageLoadTime: 3.0,
    waitBetweenActions: 0.05,`
        );
        modified = true;
        console.log('   ✅ DEFAULT_BROWSER_CONTEXT_CONFIG 优化成功');
    }

    if (modified) {
        fs.writeFileSync(MCP_FILE, content, 'utf-8');
        mcpModified++;
        console.log('   ✅ MCP browser server 文件已保存');
    } else {
        console.log('   ℹ️ 未找到匹配模式');
    }
}

console.log(`\n@agent-infra/mcp-server-browser: 成功修改 ${mcpModified}/${mcpBrowserPaths.length} 个文件`);

console.log('\n======================================');
console.log('[CUSTOM PATCH] 完成!');
console.log('======================================\n');

console.log('运行时配置（环境变量）：');
console.log('  AGENT_PAUSE_ENABLED=true/false  开关暂停/恢复');
console.log('  AGENT_PAUSE_WAIT_MS=500         工具执行后等待多久再暂停');
console.log('  AGENT_PRESS_DURATION_MS=30      按键/点击持续时间');
console.log('');
console.log('暂停/恢复逻辑：');
console.log('  1. Agent Loop Start → 第一次创建 CDP client（不暂停）');
console.log('  2. 工具执行前 → 恢复网页 + 开始录屏');
console.log('  3. 工具执行后 → 等待 AGENT_PAUSE_WAIT_MS ms → 暂停网页 + 停止录屏');
console.log('  4. 下一轮 Agent Loop → 页面已暂停，直接截图');
console.log('');
console.log('录屏功能：');
console.log('  AGENT_RECORD_ENABLED=true       开启录屏（默认 false）');
console.log('  AGENT_RECORD_FPS=10             录制帧率');
console.log('  AGENT_RECORD_QUALITY=80         JPEG 质量 (1-100)');
console.log('  AGENT_RECORD_OUTPUT=./recordings 视频输出目录');
console.log('  视频输出: <output>/agent_<sessionId>_<timestamp>.mp4');
console.log('');

// ============================================
// 7. 注入 LLM 调用日志（输入/输出详情 + 保存原始数据到文件）
// ============================================
console.log('\n======================================');
console.log('[LLM LOG PATCH] 注入 LLM 调用日志...');
console.log('======================================\n');

let llmLogModified = 0;

for (const DIST_FILE of possiblePaths) {
    console.log(`处理: ${DIST_FILE.substring(__dirname.length)}`);

    let content = fs.readFileSync(DIST_FILE, 'utf-8');

    // 检测是否已注入 LLM 日志
    const hasLLMLogPatch = content.includes('[LLM-LOG-PATCH]');

    if (hasLLMLogPatch) {
        console.log('   ⚠️ LLM 日志已注入，跳过');
        llmLogModified++;
        continue;
    }

    let modified = false;

    // 修改 onLLMRequest - 注入请求日志 + 保存原始数据
    // 编译后代码格式（多行）:
    //   onLLMRequest(id, payload) {
    //       var _this_messageHistoryDumper;
    //       null == (_this_messageHistoryDumper = this.messageHistoryDumper) || _this_messageHistoryDumper.addRequestTrace(id, payload);
    //   }
    const llmRequestPattern = /onLLMRequest\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*\{[\s\S]*?addRequestTrace\s*\([^)]*\)\s*;?\s*\}/g;
    if (llmRequestPattern.test(content)) {
        llmRequestPattern.lastIndex = 0; // 重置 lastIndex，否则 replace 会从 test 停止的位置开始
        content = content.replace(
            llmRequestPattern,
            `onLLMRequest($1, $2) {
                var _this_messageHistoryDumper; null == (_this_messageHistoryDumper = this.messageHistoryDumper) || _this_messageHistoryDumper.addRequestTrace($1, $2);
                
                // [LLM-LOG-PATCH] 打印详细的 LLM 请求日志 + 保存原始数据
                const _fs = require('fs');
                const _path = require('path');
                
                // 初始化调用计数器（per-session，避免跨 session 计数连续）
                if (!global.__LLM_COUNTERS__) global.__LLM_COUNTERS__ = {};
                if (!global.__LLM_COUNTERS__[$1]) global.__LLM_COUNTERS__[$1] = 0;
                global.__LLM_COUNTERS__[$1]++;
                const callIndex = global.__LLM_COUNTERS__[$1];
                
                // 日志输出目录
                // 优先级: per-session outputDir (from session creation payload) > AGENT_LLM_LOG_DIR > AGENT_RECORD_OUTPUT/../llm_logs > ./llm_logs
                let logDir = null;
                if (global.__SESSION_OUTPUT_DIRS__ && global.__SESSION_OUTPUT_DIRS__[$1]) {
                    logDir = _path.join(global.__SESSION_OUTPUT_DIRS__[$1], 'llm_logs');
                }
                if (!logDir) logDir = process.env.AGENT_LLM_LOG_DIR;
                if (!logDir && process.env.AGENT_RECORD_OUTPUT) {
                    logDir = _path.join(_path.dirname(process.env.AGENT_RECORD_OUTPUT), 'llm_logs');
                }
                if (!logDir) {
                    logDir = _path.join(process.cwd(), 'llm_logs');
                }
                if (!_fs.existsSync(logDir)) {
                    try { _fs.mkdirSync(logDir, { recursive: true }); } catch (e) {}
                }
                
                // 生成时间戳
                const timestamp = new Date().toISOString().replace(/[:.]/g, '-');
                
                this.logger.info('========== LLM REQUEST [#' + callIndex + '] ==========');
                this.logger.info('Session ID: ' + $1);
                this.logger.info('Provider: ' + $2.provider);
                this.logger.info('Base URL: ' + ($2.baseURL || 'default'));
                
                // 打印请求详情（不包含图片base64以避免日志过大）
                const requestInfo = {
                    model: $2.request?.model,
                    temperature: $2.request?.temperature,
                    max_tokens: $2.request?.max_tokens,
                    stream: $2.request?.stream,
                    messageCount: $2.request?.messages?.length || 0,
                };
                this.logger.info('Request Info: ' + JSON.stringify(requestInfo, null, 2));
                
                // 打印每条消息的概要（过滤掉图片内容）
                if ($2.request?.messages) {
                    this.logger.info('Messages:');
                    $2.request.messages.forEach((msg, index) => {
                        const role = msg.role || 'unknown';
                        let contentPreview = '';
                        
                        if (typeof msg.content === 'string') {
                            contentPreview = msg.content.substring(0, 500) + (msg.content.length > 500 ? '...' : '');
                        } else if (Array.isArray(msg.content)) {
                            contentPreview = msg.content.map((part) => {
                                if (part.type === 'text') {
                                    return '[text: ' + (part.text || '').substring(0, 200) + ((part.text || '').length > 200 ? '...' : '') + ']';
                                } else if (part.type === 'image_url') {
                                    return '[image_url: <base64 ' + ((part.image_url?.url || '').length) + ' chars>]';
                                }
                                return '[' + part.type + ']';
                            }).join(', ');
                        }
                        
                        this.logger.info('  [' + index + '] ' + role + ': ' + contentPreview);
                    });
                }
                
                // 保存完整的原始请求到文件（包含 base64 图片）
                const saveRaw = process.env.AGENT_LLM_SAVE_RAW !== 'false';
                if (saveRaw) {
                    try {
                        const requestFile = _path.join(logDir, callIndex.toString().padStart(4, '0') + '_' + timestamp + '_request.json');
                        const rawRequest = {
                            callIndex: callIndex,
                            timestamp: new Date().toISOString(),
                            sessionId: $1,
                            provider: $2.provider,
                            baseURL: $2.baseURL,
                            request: $2.request
                        };
                        _fs.writeFileSync(requestFile, JSON.stringify(rawRequest, null, 2));
                        this.logger.info('📁 Raw request saved to: ' + requestFile);
                    } catch (saveErr) {
                        this.logger.warn('Failed to save raw request: ' + saveErr.message);
                    }
                }
                
                this.logger.info('================================');
            }`
        );
        modified = true;
        console.log('   ✅ onLLMRequest 日志注入成功');
    } else {
        console.log('   ℹ️ 未找到 onLLMRequest 匹配模式');
    }

    // 修改 onLLMResponse - 注入响应日志 + 保存原始数据
    // 编译后代码格式（多行）:
    //   onLLMResponse(id, payload) {
    //       var _this_messageHistoryDumper;
    //       null == (_this_messageHistoryDumper = this.messageHistoryDumper) || _this_messageHistoryDumper.addResponseTrace(id, payload);
    //   }
    const llmResponsePattern = /onLLMResponse\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*\{[\s\S]*?addResponseTrace\s*\([^)]*\)\s*;?\s*\}/g;
    if (llmResponsePattern.test(content)) {
        llmResponsePattern.lastIndex = 0; // 重置 lastIndex，否则 replace 会从 test 停止的位置开始
        content = content.replace(
            llmResponsePattern,
            `onLLMResponse($1, $2) {
                var _this_messageHistoryDumper; null == (_this_messageHistoryDumper = this.messageHistoryDumper) || _this_messageHistoryDumper.addResponseTrace($1, $2);
                
                // [LLM-LOG-PATCH] 打印详细的 LLM 响应日志 + 保存原始数据
                const _fs = require('fs');
                const _path = require('path');
                
                // 获取当前调用计数（per-session）
                const callIndex = (global.__LLM_COUNTERS__ && global.__LLM_COUNTERS__[$1]) || 0;
                
                // 日志输出目录（same logic as onLLMRequest）
                let logDir = null;
                if (global.__SESSION_OUTPUT_DIRS__ && global.__SESSION_OUTPUT_DIRS__[$1]) {
                    logDir = _path.join(global.__SESSION_OUTPUT_DIRS__[$1], 'llm_logs');
                }
                if (!logDir) logDir = process.env.AGENT_LLM_LOG_DIR;
                if (!logDir && process.env.AGENT_RECORD_OUTPUT) {
                    logDir = _path.join(_path.dirname(process.env.AGENT_RECORD_OUTPUT), 'llm_logs');
                }
                if (!logDir) {
                    logDir = _path.join(process.cwd(), 'llm_logs');
                }

                // 生成时间戳
                const timestamp = new Date().toISOString().replace(/[:.]/g, '-');

                this.logger.info('========== LLM RESPONSE [#' + callIndex + '] ==========');
                this.logger.info('Session ID: ' + $1);
                this.logger.info('Provider: ' + $2.provider);
                
                const response = $2.response;
                if (response) {
                    this.logger.info('Response ID: ' + response.id);
                    this.logger.info('Model: ' + response.model);
                    
                    // 打印 usage 信息（token 消耗）
                    if (response.usage) {
                        this.logger.info('Token Usage: ' + JSON.stringify(response.usage, null, 2));
                    }
                    
                    // 打印每个 choice 的内容
                    if (response.choices && response.choices.length > 0) {
                        response.choices.forEach((choice, index) => {
                            this.logger.info('Choice [' + index + ']:');
                            this.logger.info('  Finish Reason: ' + choice.finish_reason);
                            
                            const message = choice.message;
                            if (message) {
                                this.logger.info('  Role: ' + message.role);
                                
                                // 打印文本内容
                                if (message.content) {
                                    const contentPreview = message.content.substring(0, 1000) + 
                                        (message.content.length > 1000 ? '...(truncated)' : '');
                                    this.logger.info('  Content: ' + contentPreview);
                                }
                                
                                // 打印工具调用信息
                                if (message.tool_calls && message.tool_calls.length > 0) {
                                    this.logger.info('  Tool Calls (' + message.tool_calls.length + '):');
                                    message.tool_calls.forEach((toolCall, tcIndex) => {
                                        this.logger.info('    [' + tcIndex + '] ID: ' + toolCall.id);
                                        this.logger.info('    [' + tcIndex + '] Type: ' + toolCall.type);
                                        if (toolCall.function) {
                                            this.logger.info('    [' + tcIndex + '] Function Name: ' + toolCall.function.name);
                                            // 打印函数参数（可能较长）
                                            const argsPreview = (toolCall.function.arguments || '').substring(0, 500) + 
                                                ((toolCall.function.arguments || '').length > 500 ? '...(truncated)' : '');
                                            this.logger.info('    [' + tcIndex + '] Arguments: ' + argsPreview);
                                        }
                                    });
                                }
                            }
                        });
                    }
                }
                
                // 保存完整的原始响应到文件
                const saveRaw = process.env.AGENT_LLM_SAVE_RAW !== 'false';
                if (saveRaw) {
                    try {
                        const responseFile = _path.join(logDir, callIndex.toString().padStart(4, '0') + '_' + timestamp + '_response.json');
                        const rawResponse = {
                            callIndex: callIndex,
                            timestamp: new Date().toISOString(),
                            sessionId: $1,
                            provider: $2.provider,
                            response: $2.response
                        };
                        _fs.writeFileSync(responseFile, JSON.stringify(rawResponse, null, 2));
                        this.logger.info('📁 Raw response saved to: ' + responseFile);
                    } catch (saveErr) {
                        this.logger.warn('Failed to save raw response: ' + saveErr.message);
                    }
                }
                
                this.logger.info('==================================');
            }`
        );
        modified = true;
        console.log('   ✅ onLLMResponse 日志注入成功');
    } else {
        console.log('   ℹ️ 未找到 onLLMResponse 匹配模式');
    }

    if (modified) {
        fs.writeFileSync(DIST_FILE, content, 'utf-8');
        llmLogModified++;
        console.log('   ✅ LLM 日志补丁已保存');
    }
}

console.log(`\n[LLM LOG PATCH] 完成: 成功修改 ${llmLogModified}/${possiblePaths.length} 个文件`);
console.log('');
console.log('LLM 日志功能说明：');
console.log('  - 每次模型调用时会打印 ========== LLM REQUEST/RESPONSE [#N] ========== 分隔线');
console.log('  - 请求日志包含: Session ID, Provider, Base URL, Model, Temperature, 消息列表概要');
console.log('  - 响应日志包含: Session ID, Provider, Model, Token Usage, Content, Tool Calls');
console.log('  - 图片内容在控制台显示为 [image_url: <base64 N chars>]');
console.log('');
console.log('原始数据保存功能：');
console.log('  AGENT_LLM_SAVE_RAW=true/false    是否保存原始数据（默认 true）');
console.log('  日志默认保存到与录屏目录(AGENT_RECORD_OUTPUT)同级的 llm_logs/ 下');
console.log('  运行结束后由 Python 端收集到 .verifier/llm_logs/attempt{N}/ 目录');
console.log('');
console.log('临时文件格式（运行时）：');
console.log('  {AGENT_RECORD_OUTPUT}/../llm_logs/0001_xxx_request.json');
console.log('  {AGENT_RECORD_OUTPUT}/../llm_logs/0001_xxx_response.json');
console.log('');
console.log('最终文件位置（收集后）：');
console.log('  {project}/.verifier/llm_logs/attempt{N}/0001_xxx_request.json');
console.log('  {project}/.verifier/llm_logs/attempt{N}/0001_xxx_response.json');
console.log('');

// ============================================
// 8. 修改 @tarko/agent-server: 存储 per-session outputDir
// ============================================
console.log('\n======================================');
console.log('[SESSION-OUTPUT PATCH] 注入 per-session outputDir 存储...');
console.log('======================================\n');

const agentServerPaths = [];
try {
    const nodeModulesBase = path.join(__dirname, 'node_modules/.pnpm');
    if (fs.existsSync(nodeModulesBase)) {
        const dirs = fs.readdirSync(nodeModulesBase).filter(d => d.startsWith('@tarko+agent-server@'));
        for (const dir of dirs) {
            const distPath = path.join(nodeModulesBase, dir, 'node_modules/@tarko/agent-server/dist/index.js');
            if (fs.existsSync(distPath)) agentServerPaths.push(distPath);
        }
    }
} catch (e) {
    console.log('搜索 @tarko/agent-server 时出错:', e.message);
}
// Also check the local workspace version
const localAgentServer = path.join(__dirname, 'tarko/agent-server/dist/index.js');
if (fs.existsSync(localAgentServer)) agentServerPaths.push(localAgentServer);

console.log(`找到 ${agentServerPaths.length} 个 @tarko/agent-server dist 文件`);

for (const serverPath of agentServerPaths) {
    const shortPath = serverPath.substring(__dirname.length);
    console.log(`处理: ${shortPath}`);
    let serverContent = fs.readFileSync(serverPath, 'utf-8');

    if (serverContent.includes('[CUSTOM-SESSION-OUTPUT]')) {
        console.log('   ⚠️ 已注入过，跳过');
        continue;
    }

    const sessionPattern = /const \{ runtimeSettings, agentOptions \} = req\.body;\s*\n\s*const sessionId = nanoid\(\);/;
    if (sessionPattern.test(serverContent)) {
        serverContent = serverContent.replace(
            sessionPattern,
            `const { runtimeSettings, agentOptions, outputDir } = req.body;
                const sessionId = nanoid();
                // [CUSTOM-SESSION-OUTPUT] Store per-session output dir for screencast/llm_logs
                if (outputDir) {
                    if (!global.__SESSION_OUTPUT_DIRS__) global.__SESSION_OUTPUT_DIRS__ = {};
                    global.__SESSION_OUTPUT_DIRS__[sessionId] = outputDir;
                    console.log('[SESSION-CREATE] Stored outputDir for ' + sessionId + ': ' + outputDir);
                }`
        );
        fs.writeFileSync(serverPath, serverContent, 'utf-8');
        console.log('   ✅ per-session outputDir 存储注入成功');
    } else {
        console.log('   ❌ 未找到 createSession 匹配模式');
    }
}

console.log(`\n[SESSION-OUTPUT PATCH] 完成`);