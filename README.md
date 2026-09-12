# 口播生成器 · 云端版

把一条口播视频拆成五步、串成一条自动流水线：**①选题 → ②深度调研 → ③统一成稿 → ④分段合成 TTS → ⑤数字人视频 + 自动剪辑**。

云端 TTS 与数字人视频走 [RunningHub](https://www.runninghub.ai?inviteCode=rh-v1655)（**本机无需显卡**），精剪用 [HyperFrames](https://github.com/heygen-com/hyperframes) 组件货架做包装，全部可 AI 选配或手动指定。

> 作者：**天工开帧** · 一个兴趣使然的 AI 动画师
> 抖音 / X / YouTube / B站 / GitHub 见页面底部（程序内也有入口）

---

## ✨ 能做什么

- **五阶段流水线**：灵感收集 → 联网调研取证 → 统一成稿 → 30~40 字语义分段合成 → 数字人口播视频 → 自动粗剪 + 精剪
- **云端 TTS**：无需本地模型；支持自定义音色克隆（上传参考音频 + 对应文本），不传则用内置默认音色
- **数字人工作台**：形象图库拖拽绑定、一键批量生成、文本直生成 TTS、任务状态实时反馈
- **HyperFrames 精剪**：40+ 已适配包装组件（标题 / 字幕 / 氛围 / 信息条 / 转场 / 片尾 / 数据 / 对比 / 清单），支持 AI 选配、版本回退、按反馈重剪
- **🎁 彩蛋**：任务运行时页面留白处会蹲一只 Live2D 黑猫 —— 点它看看作者是谁 ☕

## 🚀 快速开始

### 方式一：下载完整包（推荐，零依赖）

到本仓库的 **[Releases](../../releases)** 下载 `口播生成器-云端版.zip`，解压后双击 `koubo-studio-cloud.exe` 即可（已内置 FFmpeg、Node.js 与全部运行库，无需安装任何环境）。

首次运行会：
1. 自动选端口并打开浏览器（默认 http://127.0.0.1:8795/）
2. 自动生成 `config.json` 与 `projects\` 目录

然后到界面「⚙ 设置」填两样东西：

| 配置 | 说明 |
|---|---|
| **LLM API Key** | 选题 / 调研 / 成稿用，任意 OpenAI 兼容接口（推荐 DeepSeek：`https://api.deepseek.com` + `deepseek-flash`） |
| **RunningHub API Key** | 云端 TTS + 数字人视频共用（注册：[邀请链接](https://www.runninghub.ai?inviteCode=rh-v1655) 注册可得 1000 RH 币） |

> 💡 精剪（第五步）**首次运行会联网下载 HyperFrames**（约 33MB，之后缓存）；完整包已内置 Node.js，无需你安装。

### 方式二：从源码运行

```bash
pip install fastapi "uvicorn[standard]" httpx numpy
python app.py          # 自动选端口并打开浏览器
```

需自备 **FFmpeg**（加入 PATH 或放到程序目录）与 **Node.js**（精剪用）。

## 📁 目录说明

```
app.py                  主程序(单文件 FastAPI：全部后端 + 内嵌前端)
skills/                 选题/调研/成稿 的提示词技能(可自行增删)
hf-library/             HyperFrames 官方货架组件(已适配的中文包装件)
assets/hijiki/          🐈 彩蛋 Live2D 黑猫模型
assets/thanks/          作者收款码(喜欢的话可以请我喝杯咖啡 ☕)
vendor/gsap.min.js      动画库本地副本(渲染零联网)
config.example.json     配置模板(复制为 config.json 后填写)
tools/                  自测与诊断脚本
```

运行时生成：`config.json`（含 API Key，**请勿外传**）、`projects\`（你的项目数据）。

## ⚠️ 使用提醒

- 本程序调用第三方云服务（RunningHub / LLM），**需要能访问 `www.runninghub.ai` 与结果下载域名 `*.myqcloud.com`**
- 生成的音视频由 AI 合成，请遵守所在平台对 AI 生成内容的标注规定（程序内置「本视频由 AI 合成」标识选项）
- 请勿使用他人的声音/肖像素材制作内容

## 📜 许可与第三方

- 本项目采用 **PolyForm Noncommercial License 1.0.0**：个人/非商业使用免费，**禁止转售或商业包装**（详见 [LICENSE.md](LICENSE.md)）；商业授权请联系作者
- 第三方组件（FFmpeg / Node.js / HyperFrames / Live2D hijiki / GSAP 等）的版权与许可见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)

---

**喜欢这个项目的话**，可以请我喝杯咖啡 ☕（收款码见程序内彩蛋弹窗，或 `assets/thanks/`）—— 也可以在 [抖音](https://v.douyin.com/BgCJXnllitM/) / [B站](https://space.bilibili.com/11946287) / [YouTube](https://www.youtube.com/@jintaoyi) 找到我。
