# RunningHub AI - 智能图片优化工具 (Streamlit版)

这是原 Tkinter 桌面应用的 Streamlit Cloud 在线版本，完整保留了所有功能。

## 功能特性

✨ **核心功能**
- 🎨 AI智能图片优化处理
- 📤 支持多文件批量上传
- ⚡ 并发队列处理（最多同时3个任务）
- 📊 实时进度显示
- 💾 一键下载优化后的图片
- 🔄 自动任务队列管理

## 部署到 Streamlit Cloud

### 方法一：直接部署

1. 在 GitHub 创建新仓库，上传以下文件：
   - `app.py` (主应用文件)
   - `requirements.txt` (依赖文件)
   - `.streamlit/config.toml` (配置文件)

2. 访问 [Streamlit Cloud](https://streamlit.io/cloud)

3. 点击 "New app"

4. 选择你的仓库和分支

5. Main file path 填写: `app.py`

6. 点击 "Deploy"

### 方法二：使用命令行

```bash
# 1. 安装 Streamlit
pip install streamlit

# 2. 本地测试
streamlit run app.py

# 3. 部署到云端（需要先登录）
streamlit cloud deploy app.py
```

## 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 运行应用
streamlit run app.py
```

应用将在浏览器中自动打开: http://localhost:8501

## 文件结构

```
.
├── app.py                    # 主应用文件
├── requirements.txt          # Python依赖
├── .streamlit/
│   └── config.toml          # Streamlit配置
└── README.md                # 说明文档
```

## 与原版对比

### 保留的功能
✅ 所有核心功能完全保留
✅ 队列批量处理机制
✅ 并发控制（最多3个）
✅ 实时进度显示
✅ 任务状态管理
✅ 图片预览和下载
✅ API配置预设

### 优化改进
🎯 Web界面，无需安装
🌐 随时随地访问
📱 响应式设计，支持移动端
🚀 更快的部署和分享
☁️ 云端运行，不占用本地资源

## 技术栈

- **前端框架**: Streamlit
- **图片处理**: Pillow (PIL)
- **HTTP请求**: Requests
- **并发处理**: Threading

## API配置

应用已预配置以下参数：
- API Key: `c95f4c4d2703479abfbc55eefeb9bb71`
- WebApp ID: `1947599512657453057`
- 节点信息: 已配置图片输入和提示词

## 使用说明

1. **上传图片**: 点击上传区域选择一张或多张图片
2. **添加到队列**: 点击"添加到处理队列"按钮
3. **自动处理**: 系统会自动处理队列中的任务（最多同时3个）
4. **查看结果**: 处理完成后可在右侧查看优化后的图片
5. **下载图片**: 点击下载按钮保存优化后的图片

## 注意事项

- 🖼️ 支持格式: PNG, JPG, JPEG, WEBP
- 📦 最大上传: 200MB
- ⏱️ 处理时间: 每张图片约2-3分钟
- 🔄 并发限制: 最多同时处理3张图片

## 故障排除

**问题**: 上传失败
- 检查图片格式是否支持
- 确认文件大小不超过限制

**问题**: 处理超时
- RunningHub服务器可能繁忙，请稍后重试
- 检查网络连接

**问题**: 图片无法下载
- 清除浏览器缓存
- 尝试其他浏览器

## 许可证

本项目基于原 Tkinter 应用改编，保留所有原始功能。

## 联系方式

如有问题或建议，欢迎反馈！

---

**享受智能图片优化的便捷体验！** 🎨✨
