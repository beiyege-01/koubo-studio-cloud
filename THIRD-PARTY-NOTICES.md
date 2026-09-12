# 第三方组件与许可声明 Third-Party Notices

本项目使用了以下第三方组件，其版权与许可归各自作者所有：

| 组件 | 许可 | 说明 |
|---|---|---|
| **FFmpeg / FFprobe** | GPL v3 | 音视频处理。**不再随源码仓库分发**，随 Releases 的完整包分发时附 `ffmpeg-LICENSE.txt`；源码/构建脚本见 https://ffmpeg.org/ 与 https://www.gyan.dev/ffmpeg/builds/ |
| **Node.js** | MIT | 精剪（HyperFrames）运行环境。随 Releases 的完整包分发，许可见 https://github.com/nodejs/node/blob/main/LICENSE |
| **HyperFrames** | 见官方仓库 | 精剪组件货架与渲染器：https://github.com/heygen-com/hyperframes （`hf-library/` 为其官方 registry 组件） |
| **Live2D 看板娘模型 hijiki** | 模型自带授权条款 | 彩蛋黑猫（`assets/hijiki/`）。该模型为其原作者发布之免费 Live2D 模型，**使用/再分发请遵守原模型授权**；如你有疑义，可删除 `assets/hijiki/` 目录，程序会自动跳过彩蛋 |
| **Live2D Cubism SDK / L2Dwidget** | Live2D 官方条款 | Web 端渲染运行时（`assets/hijiki/live2d/`） |
| **GSAP** | 标准免费许可 | 动画库（`vendor/gsap.min.js`），https://gsap.com/ |
| **Python / FastAPI / Uvicorn / httpx / pydantic** | PSF / MIT / BSD | 运行时与 Web 框架 |

## 本项目的第三方服务

- **RunningHub**（云端 TTS 与数字人视频）：用户需自备账号与 API Key，使用其服务须遵守 RunningHub 的服务条款
- **LLM 接口**（如 DeepSeek）：用户需自备 API Key
