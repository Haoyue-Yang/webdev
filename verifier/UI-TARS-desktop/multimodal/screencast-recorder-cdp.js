/**
 * screencast-recorder-cdp.js - Agent 交互录屏模块（CDP 非阻塞版本）
 * 
 * 改进：
 * - 使用 CDP Page.startScreencast 被动接收帧，不阻塞页面交互
 * - 原版 page.screenshot() 是主动截图，会阻塞 JS 执行
 * 
 * 功能：
 * - 在工具执行时录制浏览器画面（CDP Page.screencastFrame 被动推送）
 * - 模型思考时暂停录制
 * - 最终合成流畅的交互视频（无思考延时）
 * 
 * 环境变量：
 * - AGENT_RECORD_ENABLED=true    开启录屏功能
 * - AGENT_RECORD_FPS=10          录制帧率（默认10fps）
 * - AGENT_RECORD_QUALITY=80      JPEG 质量（1-100，默认80）
 * - AGENT_RECORD_OUTPUT=./       视频输出目录
 */

const fs = require('fs');
const path = require('path');
const { spawn, execSync } = require('child_process');

class ScreencastRecorder {
    constructor(options = {}) {
        this.enabled = process.env.AGENT_RECORD_ENABLED === 'true';
        this.fps = parseInt(process.env.AGENT_RECORD_FPS) || 10;
        this.quality = parseInt(process.env.AGENT_RECORD_QUALITY) || 80;
        this.outputDir = process.env.AGENT_RECORD_OUTPUT || './recordings';

        // 是否从 init 时就开始持续录制
        this.recordFromInit = process.env.AGENT_RECORD_FROM_INIT === 'true';

        // 录屏策略: cdp_legacy (CDP+Legacy混合) 或 cdp_only (全CDP)
        // cdp_legacy: 工具执行时用CDP，暂停等待期间用legacy主动截图
        // cdp_only: 全程只用CDP，不做fallback截图
        this.strategy = process.env.AGENT_RECORD_STRATEGY || 'cdp_legacy';

        this.cdpClient = null;
        this.isRecording = false;
        this.isScreencastActive = false;  // CDP screencast 是否已启动
        this.frames = [];
        this.currentToolName = null;
        this.sessionId = null;
        this.frameDir = null;
        this._page = null;

        // 帧计数和去重
        this._frameIndex = 0;
        this._lastFrameTime = 0;
        this._minFrameInterval = Math.floor(1000 / this.fps);  // 最小帧间隔

        // 录制统计
        this.stats = {
            totalFrames: 0,
            droppedFrames: 0,  // 因为频率过高被丢弃的帧
            recordingSegments: 0,
            startTime: null,
            endTime: null
        };

        if (this.enabled) {
            const strategyLabel = this.strategy === 'cdp_only' ? '全CDP' : 'CDP+Legacy混合';
            console.log('[SCREENCAST-CDP] 录屏功能已启用');
            console.log(`[SCREENCAST-CDP]   FPS: ${this.fps}, Quality: ${this.quality}`);
            console.log(`[SCREENCAST-CDP]   策略: ${strategyLabel}`);
            console.log(`[SCREENCAST-CDP]   输出目录: ${this.outputDir}`);
            console.log(`[SCREENCAST-CDP]   录制模式: ${this.recordFromInit ? '从初始化开始持续录制' : '仅工具执行时录制'}`);

            this._setupExitHandler();
        }
    }

    /**
     * 设置进程退出时自动 finalize
     */
    _setupExitHandler() {
        const self = this;
        let finalizing = false;

        const exitHandler = async (signal) => {
            if (finalizing) return;
            finalizing = true;

            console.log(`[SCREENCAST-CDP] 收到退出信号 (${signal})，正在生成视频...`);

            try {
                await self.finalize();
            } catch (e) {
                console.log('[SCREENCAST-CDP] 退出时生成视频失败:', e.message);
            }

            if (signal === 'SIGINT' || signal === 'SIGTERM') {
                process.exit(0);
            }
        };

        process.on('SIGINT', () => exitHandler('SIGINT'));
        process.on('SIGTERM', () => exitHandler('SIGTERM'));
        process.on('beforeExit', () => exitHandler('beforeExit'));

        if (typeof global !== 'undefined') {
            global.__SCREENCAST_FINALIZE__ = () => self.finalize();
        }
    }

    /**
     * 初始化录屏器
     * @param {Object} cdpClient - CDP 客户端
     * @param {string} projectId - 项目ID
     * @param {Object} page - Puppeteer page 对象（保留兼容）
     */
    async init(cdpClient, projectId = 'default', page = null) {
        if (!this.enabled) return;

        console.log(`[SCREENCAST-CDP] init() called: cdpClient=${!!cdpClient}, existing cdpClient=${!!this.cdpClient}`);

        // [FIX] 如果有旧的 CDP client 且 screencast 正在运行，先停止并清理
        if (this.cdpClient && this.isScreencastActive) {
            console.log('[SCREENCAST-CDP] 清理旧的 CDP screencast...');
            try {
                await this.cdpClient.send('Page.stopScreencast');
            } catch (e) {
                // 旧 client 可能已经无效，忽略错误
            }
            // 移除旧的监听器
            if (this._boundFrameHandler) {
                try {
                    this.cdpClient.off('Page.screencastFrame', this._boundFrameHandler);
                } catch (e) {
                    // 忽略
                }
            }
        }

        // [FIX] 重置 screencast 状态，确保新的 screencast 可以启动
        this.isScreencastActive = false;
        this._boundFrameHandler = null;

        this.cdpClient = cdpClient;
        this._page = page;
        this.sessionId = `${projectId}_${Date.now()}`;
        this.stats.startTime = Date.now();

        // [FIX] 每次 init 时重置帧计数器，避免跨 case 帧序号连续
        this._frameIndex = 0;
        this._lastFrameTime = 0;
        this.frames = [];
        this.isRecording = false;
        this.stats.totalFrames = 0;
        this.stats.droppedFrames = 0;
        this.stats.recordingSegments = 0;

        // 创建帧存储目录
        this.frameDir = path.join(this.outputDir, 'frames', this.sessionId);
        fs.mkdirSync(this.frameDir, { recursive: true });

        console.log(`[SCREENCAST-CDP] 初始化完成，会话ID: ${this.sessionId}`);
        console.log(`[SCREENCAST-CDP] 采集模式: CDP Page.screencastFrame（非阻塞）`);

        // 启动 CDP screencast（持续运行，但只在 isRecording=true 时保存帧）
        await this._startCDPScreencast();

        // 如果设置了从初始化开始录制
        if (this.recordFromInit) {
            console.log(`[SCREENCAST-CDP] 🎬 从初始化开始持续录制...`);
            this.currentToolName = '__continuous__';
            this.isRecording = true;
            this.stats.recordingSegments++;
        }
    }

    /**
     * 获取当前实际的 viewport 尺寸
     * 优先使用环境变量指定的尺寸，然后尝试从 Puppeteer page 获取
     * @returns {{width: number, height: number}} 视口尺寸
     */
    async _getCurrentViewport() {
        // 首先检查环境变量，允许用户强制指定尺寸
        const envWidth = parseInt(process.env.AGENT_RECORD_VIEWPORT_WIDTH);
        const envHeight = parseInt(process.env.AGENT_RECORD_VIEWPORT_HEIGHT);

        if (envWidth && envHeight) {
            console.log(`[SCREENCAST-CDP] 使用环境变量指定的 viewport: ${envWidth}x${envHeight}`);
            return { width: envWidth, height: envHeight };
        }

        // 使用 Puppeteer page.viewport() 的值（这是 legacy screenshot 使用的尺寸）
        let width = 1280;
        let height = 800;

        if (this._page) {
            const viewport = this._page.viewport();
            if (viewport && viewport.width && viewport.height) {
                width = viewport.width;
                height = viewport.height;
                console.log(`[SCREENCAST-CDP] 使用 Puppeteer viewport: ${width}x${height}`);
                return { width, height };
            }
        }

        // 如果 Puppeteer viewport 不可用，通过 CDP 获取
        if (this.cdpClient) {
            try {
                const result = await this.cdpClient.send('Page.getViewportInfo');
                if (result && result.layoutViewport) {
                    width = result.layoutViewport.width || width;
                    height = result.layoutViewport.height || height;
                    console.log(`[SCREENCAST-CDP] 使用 CDP viewport: ${width}x${height}`);
                    return { width, height };
                }
            } catch (e) {
                console.log(`[SCREENCAST-CDP] 通过 CDP 获取 viewport 失败: ${e.message}`);
            }
        }

        console.log(`[SCREENCAST-CDP] 使用默认 viewport: ${width}x${height}`);
        return { width, height };
    }

    /**
     * 启动 CDP Screencast（只启动一次，持续运行）
     * 注意：CDP screencast 启动后尺寸就固定了，如果页面大小变化需要重启
     */
    async _startCDPScreencast() {
        if (!this.cdpClient || this.isScreencastActive) return;

        try {
            // 监听 screencastFrame 事件
            this._boundFrameHandler = this._handleScreencastFrame.bind(this);
            this.cdpClient.on('Page.screencastFrame', this._boundFrameHandler);

            // 等待一下让页面完全初始化（Playwright 浏览器需要更多时间）
            await new Promise(r => setTimeout(r, 500));

            // [FIX] 强制设置 viewport，确保与 window-size 参数一致
            // Playwright 通过 CDP 连接时不会自动设置 viewport，需要手动设置
            if (this._page) {
                const puppeteerViewport = this._page.viewport();
                const expectedViewport = await this._getCurrentViewport();

                // 如果 Puppeteer viewport 未设置或与预期不符，强制设置
                if (!puppeteerViewport ||
                    puppeteerViewport.width !== expectedViewport.width ||
                    puppeteerViewport.height !== expectedViewport.height) {
                    console.log(`[SCREENCAST-CDP] 强制设置 viewport: ${expectedViewport.width}x${expectedViewport.height}`);
                    await this._page.setViewport({
                        width: expectedViewport.width,
                        height: expectedViewport.height,
                        deviceScaleFactor: 1
                    });

                    // 同时通过 CDP Emulation.setDeviceMetricsOverride 设置（双重保险）
                    await this.cdpClient.send('Emulation.setDeviceMetricsOverride', {
                        width: expectedViewport.width,
                        height: expectedViewport.height,
                        deviceScaleFactor: 1,
                        mobile: false
                    });
                }
            }

            // 获取 Puppeteer page 的实际 viewport 尺寸，确保与 legacy screenshot 一致
            const viewport = await this._getCurrentViewport();

            console.log(`[SCREENCAST-CDP] 使用 viewport 尺寸: ${viewport.width}x${viewport.height}`);

            // 启动 screencast
            // everyNthFrame: 1 表示每帧都推送，我们在接收端做频率控制
            await this.cdpClient.send('Page.startScreencast', {
                format: 'jpeg',
                quality: this.quality,
                maxWidth: viewport.width,
                maxHeight: viewport.height,
                everyNthFrame: 1
            });

            this.isScreencastActive = true;
            console.log(`[SCREENCAST-CDP] ✅ CDP screencast 已启动 (${viewport.width}x${viewport.height})`);

        } catch (e) {
            console.log('[SCREENCAST-CDP] ❌ 启动 CDP screencast 失败:', e.message);
        }
    }

    /**
     * 重新启动 CDP Screencast（用于页面大小变化时）
     */
    async _restartCDPScreencast() {
        if (!this.cdpClient) return;

        // 停止当前的 screencast
        await this._stopCDPScreencast();

        // 重新启动，使用新的 viewport 尺寸
        this.isScreencastActive = false;
        await this._startCDPScreencast();
    }

    /**
     * 处理 CDP 推送的帧
     */
    async _handleScreencastFrame(params) {
        const { data, metadata, sessionId } = params;

        // 必须发送 ack，否则 CDP 会停止推送
        try {
            await this.cdpClient.send('Page.screencastFrameAck', { sessionId });
        } catch (e) {
            // ack 失败通常是页面已关闭，忽略
        }

        // 如果不在录制状态，丢弃帧
        if (!this.isRecording) {
            return;
        }

        // 频率控制：避免帧率过高
        const now = Date.now();
        if (now - this._lastFrameTime < this._minFrameInterval) {
            this.stats.droppedFrames++;
            return;
        }
        this._lastFrameTime = now;

        // 保存帧
        const frameIndex = this._frameIndex++;
        const framePath = path.join(this.frameDir, `frame_${String(frameIndex).padStart(6, '0')}.jpg`);

        try {
            // data 是 base64 编码的图片
            const buffer = Buffer.from(data, 'base64');
            fs.writeFileSync(framePath, buffer);

            this.frames.push({
                index: frameIndex,
                timestamp: now,
                toolName: this.currentToolName,
                path: framePath,
                metadata: metadata  // 包含 offsetTop, pageScaleFactor, deviceWidth, deviceHeight 等
            });

            this.stats.totalFrames++;

            if (frameIndex > 0 && frameIndex % 50 === 0) {
                console.log(`[SCREENCAST-CDP] 已录制 ${frameIndex} 帧 (丢弃 ${this.stats.droppedFrames} 帧)...`);
            }

        } catch (e) {
            if (this.stats.totalFrames === 0) {
                console.log(`[SCREENCAST-CDP] 保存帧失败: ${e.message}`);
            }
        }
    }

    /**
     * 开始录制（工具执行前调用）
     * CDP screencast 持续运行，这里只是开启帧保存
     */
    async startRecording(toolName = 'unknown') {
        if (!this.enabled) return;

        // 即使已经在录制，也更新工具名
        this.currentToolName = toolName;

        console.log(`[SCREENCAST-CDP] ▶️ startRecording: cdpClient=${!!this.cdpClient}, isScreencastActive=${this.isScreencastActive}, isRecording=${this.isRecording}`);

        if (this.isRecording) {
            console.log(`[SCREENCAST-CDP]   已在录制中`);
            return;
        }

        // 如果 screencast 未启动，尝试启动
        if (this.cdpClient && !this.isScreencastActive) {
            console.log(`[SCREENCAST-CDP]   尝试启动 screencast...`);
            await this._startCDPScreencast();
        }

        if (!this.cdpClient) {
            console.log(`[SCREENCAST-CDP] ⚠️ 没有可用的 CDP client，无法录制`);
            return;
        }

        this.isRecording = true;
        this.stats.recordingSegments++;

        console.log(`[SCREENCAST-CDP] ▶️ 开始录制 (工具: ${toolName})`);
    }

    /**
     * 停止录制（工具执行后调用）
     * CDP screencast 继续运行，只是停止保存帧
     */
    async stopRecording() {
        console.log(`[SCREENCAST-CDP] ⏹️ stopRecording: enabled=${this.enabled}, isRecording=${this.isRecording}, cdpClient=${!!this.cdpClient}`);

        if (!this.enabled || !this.isRecording) return;

        this.isRecording = false;
        this.currentToolName = null;

        console.log(`[SCREENCAST-CDP] ⏸️ 暂停录制 (已保存 ${this.stats.totalFrames} 帧, 丢弃 ${this.stats.droppedFrames} 帧)`);

        // [FIX] 重置帧时间，确保下次开始录制时不会因为频率控制而丢弃帧
        this._lastFrameTime = 0;

        // 保存 metadata
        this._saveMetadata();
    }

    /**
     * 从外部传入的 buffer 添加一帧（用于 500ms 等待期间的主动截图）
     * @param {Buffer} buffer - JPEG 图片的 Buffer
     * @param {string} [source='fallback'] - 来源标识
     * @returns {boolean} 是否成功保存
     */
    addFrameFromBuffer(buffer, source = 'fallback') {
        if (!this.enabled || !this.frameDir) {
            return false;
        }

        try {
            const frameIndex = this._frameIndex++;
            const framePath = path.join(this.frameDir, `frame_${String(frameIndex).padStart(6, '0')}.jpg`);

            fs.writeFileSync(framePath, buffer);

            this.frames.push({
                index: frameIndex,
                timestamp: Date.now(),
                toolName: `${this.currentToolName || 'unknown'}_${source}`,
                path: framePath,
                metadata: { source }
            });

            this.stats.totalFrames++;
            return true;
        } catch (e) {
            console.log(`[SCREENCAST-CDP] addFrameFromBuffer failed: ${e.message}`);
            return false;
        }
    }

    /**
     * 停止 CDP Screencast
     */
    async _stopCDPScreencast() {
        if (!this.cdpClient || !this.isScreencastActive) return;

        try {
            await this.cdpClient.send('Page.stopScreencast');
            this.isScreencastActive = false;
            console.log('[SCREENCAST-CDP] CDP screencast 已停止');
        } catch (e) {
            // 忽略停止失败（页面可能已关闭）
        }
    }

    /**
     * 保存帧元数据到 metadata.json
     */
    _saveMetadata() {
        if (!this.frameDir || this.stats.totalFrames === 0) return;

        try {
            const metadataPath = path.join(this.frameDir, 'metadata.json');
            fs.writeFileSync(metadataPath, JSON.stringify({
                sessionId: this.sessionId,
                fps: this.fps,
                quality: this.quality,
                mode: 'cdp-screencast',
                stats: {
                    totalFrames: this.stats.totalFrames,
                    droppedFrames: this.stats.droppedFrames,
                    recordingSegments: this.stats.recordingSegments,
                    startTime: this.stats.startTime
                }
            }, null, 2));
        } catch (e) {
            console.log(`[SCREENCAST-CDP] ⚠️ 保存 metadata.json 失败: ${e.message}`);
        }
    }

    /**
     * 结束录制会话并生成视频
     */
    async finalize() {
        if (!this.enabled) return null;

        // 停止录制
        await this.stopRecording();

        // 停止 CDP screencast
        await this._stopCDPScreencast();

        this.stats.endTime = Date.now();
        const duration = (this.stats.endTime - this.stats.startTime) / 1000;

        console.log(`[SCREENCAST-CDP] 录制完成！`);
        console.log(`[SCREENCAST-CDP]   总帧数: ${this.stats.totalFrames}`);
        console.log(`[SCREENCAST-CDP]   丢弃帧: ${this.stats.droppedFrames}`);
        console.log(`[SCREENCAST-CDP]   录制段数: ${this.stats.recordingSegments}`);
        console.log(`[SCREENCAST-CDP]   总时长: ${duration.toFixed(1)}s`);

        if (this.stats.totalFrames === 0) {
            console.log('[SCREENCAST-CDP] 没有录制到任何帧，跳过视频生成');
            return null;
        }

        // 保存完整帧元数据
        const metadataPath = path.join(this.frameDir, 'metadata.json');
        fs.writeFileSync(metadataPath, JSON.stringify({
            sessionId: this.sessionId,
            stats: this.stats,
            fps: this.fps,
            mode: 'cdp-screencast',
            frames: this.frames.map(f => ({
                index: f.index,
                timestamp: f.timestamp,
                toolName: f.toolName
            }))
        }, null, 2));

        // 生成视频
        const videoPath = await this._generateVideo();

        return {
            videoPath,
            framesDir: this.frameDir,
            stats: this.stats
        };
    }

    /**
     * 使用 ffmpeg 生成视频
     */
    async _generateVideo() {
        const outputPath = path.join(this.outputDir, `${this.sessionId}.mp4`);

        // 检查 ffmpeg 是否可用
        try {
            execSync('which ffmpeg', { stdio: 'ignore' });
        } catch (e) {
            console.log('[SCREENCAST-CDP] ⚠️ ffmpeg 未安装，跳过视频生成');
            console.log('[SCREENCAST-CDP] 帧已保存到:', this.frameDir);
            console.log('[SCREENCAST-CDP] 可手动运行: ffmpeg -framerate 10 -i frame_%06d.jpg -c:v libx264 -pix_fmt yuv420p output.mp4');
            return null;
        }

        fs.mkdirSync(path.dirname(outputPath), { recursive: true });

        const ffmpegArgs = [
            '-y',
            '-framerate', String(this.fps),
            '-i', path.join(this.frameDir, 'frame_%06d.jpg'),
            '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            outputPath
        ];

        console.log(`[SCREENCAST-CDP] 正在生成视频: ${outputPath}`);

        return new Promise((resolve, reject) => {
            const ffmpeg = spawn('ffmpeg', ffmpegArgs, { stdio: 'pipe' });

            let stderr = '';
            ffmpeg.stderr.on('data', (data) => {
                stderr += data.toString();
            });

            ffmpeg.on('close', (code) => {
                if (code === 0) {
                    console.log(`[SCREENCAST-CDP] ✅ 视频生成成功: ${outputPath}`);

                    if (process.env.AGENT_RECORD_KEEP_FRAMES !== 'true') {
                        this._cleanupFrames();
                    }

                    resolve(outputPath);
                } else {
                    console.log(`[SCREENCAST-CDP] ❌ 视频生成失败 (code=${code})`);
                    console.log(stderr.slice(-500));
                    resolve(null);
                }
            });

            ffmpeg.on('error', (err) => {
                console.log(`[SCREENCAST-CDP] ❌ ffmpeg 执行失败: ${err.message}`);
                resolve(null);
            });
        });
    }

    /**
     * 清理帧文件
     */
    _cleanupFrames() {
        try {
            const files = fs.readdirSync(this.frameDir);
            let count = 0;
            for (const file of files) {
                if (file.endsWith('.jpg')) {
                    fs.unlinkSync(path.join(this.frameDir, file));
                    count++;
                }
            }
            console.log(`[SCREENCAST-CDP] 已清理 ${count} 个帧文件`);
        } catch (e) {
            console.log(`[SCREENCAST-CDP] 清理帧文件失败: ${e.message}`);
        }
    }
}

module.exports = {
    ScreencastRecorder,
    createRecorder: (options) => new ScreencastRecorder(options)
};