# 🎨 RunningHub AI 图片优化工具 - 网页版

将您的桌面应用转换为在线网站，**零成本、零代码基础也能部署！**

---

## 🚀 方案一：最简单 - Streamlit Cloud（推荐新手）

### ✅ 优点
- ✨ 完全免费
- 🎯 最简单，5分钟部署
- 🌐 自动获得公网域名
- 🔄 代码更新自动部署

### 📝 部署步骤

#### 1. 准备工作
- 注册 GitHub 账号：https://github.com
- 注册 Streamlit Cloud：https://streamlit.io/cloud

#### 2. 上传代码到 GitHub

**方法A：网页操作（最简单）**
1. 登录 GitHub
2. 点击右上角 "+" → "New repository"
3. 仓库名输入：`runninghub-app`
4. 选择 "Public"
5. 勾选 "Add a README file"
6. 点击 "Create repository"
7. 在新建的仓库页面，点击 "Add file" → "Upload files"
8. 把这3个文件拖进去：
   - `app.py`
   - `requirements.txt`
   - `README.md`
9. 点击 "Commit changes"

**方法B：命令行操作（如果你会用Git）**
```bash
git init
git add app.py requirements.txt README.md
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/你的用户名/runninghub-app.git
git push -u origin main
```

#### 3. 部署到 Streamlit Cloud

1. 访问：https://streamlit.io/cloud
2. 点击 "Sign in with GitHub" 登录
3. 点击 "New app"
4. 选择：
   - Repository: `你的用户名/runninghub-app`
   - Branch: `main`
   - Main file path: `app.py`
5. 点击 "Deploy!"
6. 等待1-2分钟，完成！🎉

#### 4. 获取网址
- 部署成功后会自动生成网址，格式如：
  `https://你的用户名-runninghub-app-xxxxx.streamlit.app`
- 把这个网址分享给任何人都可以访问！

---

## 🚀 方案二：本地运行（测试用）

### 步骤

1. **安装Python**（如果还没有）
   - 下载：https://www.python.org/downloads/
   - 安装时勾选 "Add Python to PATH"

2. **打开命令行**
   - Windows：按 `Win + R`，输入 `cmd`
   - Mac：打开 "终端"

3. **安装依赖**
   ```bash
   pip install streamlit requests pillow
   ```

4. **运行应用**
   ```bash
   cd 你的文件夹路径
   streamlit run app.py
   ```

5. **打开浏览器**
   - 自动打开：http://localhost:8501
   - 只有你自己能访问

---

## 🚀 方案三：其他部署选项

### A. Hugging Face Spaces（免费）
- 注册：https://huggingface.co
- 创建 Space，选择 Streamlit
- 上传文件即可

### B. Railway（有免费额度）
- 注册：https://railway.app
- 连接 GitHub 仓库
- 自动部署

### C. Render（有免费额度）
- 注册：https://render.com
- 选择 Web Service
- 连接 GitHub

---

## 📱 使用方法

1. 打开网站
2. 点击 "Browse files" 上传图片
3. 点击 "开始AI处理"
4. 等待2-3分钟
5. 下载优化后的图片

---

## ❓ 常见问题

### Q1: 为什么处理这么慢？
A: 这是调用 RunningHub 的 AI 服务，服务器处理需要时间，一般2-3分钟。

### Q2: 能同时处理多张图吗？
A: 当前版本一次处理一张。如需批量，建议多次上传。

### Q3: 图片会被保存吗？
A: 不会。处理完成后服务器会自动删除。

### Q4: 部署失败怎么办？
A: 检查以下几点：
- requirements.txt 文件格式正确
- app.py 没有语法错误
- Streamlit Cloud 日志查看错误信息

### Q5: 能自定义域名吗？
A: Streamlit Cloud 可以设置自定义域名（在设置里）。

---

## 🎯 总结

| 方案 | 难度 | 费用 | 适合场景 |
|------|------|------|----------|
| Streamlit Cloud | ⭐️ | 免费 | **首选！适合所有人** |
| 本地运行 | ⭐️⭐️ | 免费 | 自己测试 |
| Hugging Face | ⭐️⭐️ | 免费 | 备选方案 |
| Railway/Render | ⭐️⭐️⭐️ | 部分免费 | 需要更多功能 |

---

## 💡 下一步改进建议

1. **添加批量处理**：一次上传多张图
2. **自定义参数**：调整提示词
3. **历史记录**：查看之前处理的图片
4. **用户登录**：保存个人设置

需要这些功能的话，随时告诉我！

---

## 📞 需要帮助？

- Streamlit 文档：https://docs.streamlit.io
- GitHub 帮助：https://docs.github.com

**祝部署顺利！🎉**
