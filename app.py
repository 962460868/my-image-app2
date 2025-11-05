import streamlit as st
import requests
import time
from datetime import datetime
import threading
import copy
import random
import logging
import streamlit.components.v1 as components

# --- 1. 页面配置和全局设置 ---

st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 配置日志，减少噪音
logging.getLogger("tornado.access").setLevel(logging.ERROR)
logging.getLogger("tornado.application").setLevel(logging.ERROR)
logging.getLogger("tornado.general").setLevel(logging.ERROR)

# API配置
API_KEY = "9394a5c6d9454cd2b31e24661dd11c3d"
WEBAPP_ID = "1947599512657453057"
NODE_INFO = [
    {"nodeId": "38", "fieldName": "image", "fieldValue": "placeholder.png", "description": "图片输入"},
    {"nodeId": "60", "fieldName": "text", "fieldValue": "8k, high quality, high detail", "description": "正向提示词补充"},
    {"nodeId": "4", "fieldName": "text", "fieldValue": "色调艳丽,过曝,静态,细节模糊不清,字幕,风格,作品,画作,画面,静止,整体发灰,最差质量,低质量,JPEG压缩残留,丑陋的,残缺的,多余的手指,画得不好的手部,画得不好的脸部,畸形的,毁容的,形态畸形的肢体,手指融合,静止不动的画面,悲乱的背景,三条腿,背景人很多,倒着走", "description": "反向提示词"}
]

# 系统配置
MAX_CONCURRENT = 5  # 单网页最大并发数
MAX_RETRIES = 3
POLL_INTERVAL = 3
MAX_POLL_COUNT = 300
AUTO_REFRESH_INTERVAL = 6
DISPLAY_TIMEOUT_MINUTES = 3
ACTUAL_TIMEOUT_MINUTES = 15

# 并发限制错误关键词
CONCURRENT_LIMIT_ERRORS = [
    "concurrent limit", "too many requests", "rate limit",
    "队列已满", "并发限制", "服务忙碌", "CONCURRENT_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"
]

# --- 2. 优化CSS样式和JavaScript ---

st.markdown("""
<style>
    .main { background-color: #f8f9fa; }
    .stButton>button {
        width: 100%; border-radius: 6px; height: 2.5em;
        background-color: #0066cc; color: white; font-weight: 500;
        transition: all 0.2s ease;
    }
    .stButton>button:hover { 
        background-color: #0052a3; 
        transform: translateY(-1px);
    }
    .stButton>button:active {
        transform: translateY(0);
        background-color: #004080;
    }
    .download-clicked {
        background-color: #28a745 !important;
        transform: scale(0.98);
    }
    .task-card {
        background: white; border-radius: 8px; padding: 1rem; margin: 0.5rem 0;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); border-left: 4px solid #0066cc;
    }
    .success-badge { color: #28a745; font-weight: 600; }
    .error-badge { color: #dc3545; font-weight: 600; }
    .processing-badge { color: #fd7e14; font-weight: 600; }
    .queued-badge { color: #6f42c1; font-weight: 600; }
    .metric-box {
        background: white; padding: 0.8rem; border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); text-align: center; margin-bottom: 0.3rem;
    }
    .compact-info { font-size: 0.85em; color: #6c757d; margin: 0.2rem 0; }
    .real-time { 
        font-family: 'Courier New', monospace; 
        color: #495057; 
        font-weight: 500;
        background-color: #f8f9fa;
        padding: 2px 6px;
        border-radius: 3px;
    }
    .download-feedback {
        position: fixed;
        top: 20px;
        right: 20px;
        background: #28a745;
        color: white;
        padding: 10px 20px;
        border-radius: 5px;
        z-index: 1000;
        animation: slideIn 0.3s ease-out;
    }
    @keyframes slideIn {
        from { transform: translateX(100%); opacity: 0; }
        to { transform: translateX(0); opacity: 1; }
    }
</style>

<script>
// 实时时间更新
function updateElapsedTimes() {
    const timeElements = document.querySelectorAll('[data-start-time]');
    timeElements.forEach(element => {
        const startTime = parseFloat(element.getAttribute('data-start-time'));
        const displayTimeout = parseInt(element.getAttribute('data-display-timeout')) * 60;
        const now = Date.now() / 1000;
        const elapsed = now - startTime;
        
        const elapsedMinutes = Math.floor(elapsed / 60);
        const elapsedSeconds = Math.floor(elapsed % 60);
        
        let timeText = `⏱️ 已用时 ${elapsedMinutes}:${elapsedSeconds.toString().padStart(2, '0')}`;
        
        if (elapsed < displayTimeout) {
            const remaining = Math.max(0, displayTimeout - elapsed);
            const remainingMinutes = Math.floor(remaining / 60);
            const remainingSeconds = Math.floor(remaining % 60);
            timeText += ` | 预计剩余 ${remainingMinutes}:${remainingSeconds.toString().padStart(2, '0')}`;
        } else {
            timeText += ' | 处理中...';
        }
        
        element.innerHTML = timeText;
    });
}

// 下载反馈
function showDownloadFeedback() {
    const feedback = document.createElement('div');
    feedback.className = 'download-feedback';
    feedback.textContent = '✅ 下载开始！';
    document.body.appendChild(feedback);
    
    setTimeout(() => {
        feedback.remove();
    }, 2000);
}

// 页面加载完成后启动定时器
document.addEventListener('DOMContentLoaded', function() {
    setInterval(updateElapsedTimes, 1000);
});

// 对于动态加载的内容，也要启动定时器
setTimeout(() => {
    setInterval(updateElapsedTimes, 1000);
}, 1000);
</script>
""", unsafe_allow_html=True)

# --- 3. Session State管理 ---

def get_session_key():
    if 'session_id' not in st.session_state:
        st.session_state.session_id = f"s_{int(time.time())}_{random.randint(100, 999)}"
    return st.session_state.session_id

# 初始化Session State
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
if 'upload_success' not in st.session_state:
    st.session_state.upload_success = False
if 'download_clicked' not in st.session_state:
    st.session_state.download_clicked = {}
if 'task_queue' not in st.session_state:
    st.session_state.task_queue = []

# --- 4. 任务类 ---

class TaskItem:
    def __init__(self, task_id, file_data, file_name, session_id):
        self.task_id = task_id
        self.file_data = file_data
        self.file_name = file_name
        self.session_id = session_id
        self.status = "QUEUED"
        self.progress = 0
        self.result_data = None
        self.error_message = None
        self.api_task_id = None
        self.created_at = datetime.now()
        self.start_time = None
        self.elapsed_time = None
        self.retry_count = 0

# --- 5. 核心API函数 ---

def is_concurrent_limit_error(error_msg):
    """检查是否为并发限制错误"""
    error_lower = error_msg.lower()
    return any(keyword in error_lower for keyword in CONCURRENT_LIMIT_ERRORS)

def upload_file(file_data, file_name, api_key):
    """上传文件到API"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    result = response.json()
    if result.get("code") == 0:
        return result['data'] ['fileName']
    else:
        raise Exception(f"上传失败: {result.get('msg', '未知错误')}")

def run_task(api_key, webapp_id, node_info_list):
    """启动API任务"""
    url = 'https://www.runninghub.cn/task/openapi/ai-app/run'
    payload = {"apiKey": api_key, "webappId": webapp_id, "nodeInfoList": node_info_list}
    response = requests.post(url, headers={'Content-Type': 'application/json'}, 
                           json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise Exception(f"任务发起失败: {result.get('msg', '未知错误')}")
    return result['data'] ['taskId']

def get_task_status(api_key, task_id):
    """获取任务状态"""
    url = 'https://www.runninghub.cn/task/openapi/status'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=10)
    response.raise_for_status()
    return response.json().get('data')

def fetch_task_output(api_key, task_id):
    """获取任务结果"""
    url = 'https://www.runninghub.cn/task/openapi/outputs'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"] [0].get("fileUrl")
        if file_url:
            return file_url
    raise Exception(f"获取结果失败: {data.get('msg', '未找到结果')}")

def download_result_image(url):
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    return response.content

# --- 6. 任务处理逻辑 ---

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务"""
    task.status = "PROCESSING"
    task.start_time = time.time()

    try:
        task.progress = 15
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)

        task.progress = 25
        node_info_list = copy.deepcopy(node_info)
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename

        task.progress = 35
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)

        poll_count = 0
        while poll_count < MAX_POLL_COUNT:
            time.sleep(POLL_INTERVAL)
            poll_count += 1

            status = get_task_status(api_key, task.api_task_id)
            task.progress = min(90, 35 + (55 * poll_count / MAX_POLL_COUNT))

            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("API任务处理失败")

        if poll_count >= MAX_POLL_COUNT:
            raise Exception(f"任务超时 (>{ACTUAL_TIMEOUT_MINUTES}分钟)")

        task.progress = 95
        result_url = fetch_task_output(api_key, task.api_task_id)
        task.result_data = download_result_image(result_url)

        task.progress = 100
        task.status = "SUCCESS"
        task.elapsed_time = time.time() - task.start_time

    except Exception as e:
        error_msg = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0

        if (is_concurrent_limit_error(error_msg) and task.retry_count < MAX_RETRIES):
            task.retry_count += 1
            task.status = "QUEUED"
            task.progress = 0
            # 添加到队列重新处理
            st.session_state.task_queue.append(task)
            time.sleep((2 ** task.retry_count) + random.randint(1, 3))
        else:
            task.status = "FAILED"
            task.error_message = error_msg[:100]

# --- 7. 队列管理 ---

def get_stats():
    """获取统计信息"""
    processing_count = sum(1 for t in st.session_state.tasks if t.status == "PROCESSING")
    queued_count = len(st.session_state.task_queue) + sum(1 for t in st.session_state.tasks if t.status == "QUEUED")
    success_count = sum(1 for t in st.session_state.tasks if t.status == "SUCCESS")
    failed_count = sum(1 for t in st.session_state.tasks if t.status == "FAILED")
    
    return {
        'processing': processing_count,
        'queued': queued_count,
        'success': success_count,
        'failed': failed_count,
        'total': len(st.session_state.tasks)
    }

def start_new_tasks():
    """启动新任务"""
    stats = get_stats()
    available_slots = MAX_CONCURRENT - stats['processing']
    
    if available_slots <= 0:
        return
    
    # 处理队列中的任务
    for _ in range(min(available_slots, len(st.session_state.task_queue))):
        if st.session_state.task_queue:
            task = st.session_state.task_queue.pop(0)
            
            thread = threading.Thread(
                target=process_single_task,
                args=(task, API_KEY, WEBAPP_ID, NODE_INFO)
            )
            thread.daemon = True
            thread.start()
    
    # 处理状态为QUEUED的任务
    queued_tasks = [t for t in st.session_state.tasks if t.status == "QUEUED"]
    remaining_slots = MAX_CONCURRENT - stats['processing'] - len([t for t in st.session_state.tasks if t.status == "PROCESSING"])
    
    for task in queued_tasks[:remaining_slots]:
        thread = threading.Thread(
            target=process_single_task,
            args=(task, API_KEY, WEBAPP_ID, NODE_INFO)
        )
        thread.daemon = True
        thread.start()

# --- 8. 下载按钮组件 ---

def create_download_button(task):
    """创建优化的下载按钮"""
    file_size = len(task.result_data) / 1024  # KB
    button_key = f"download_{task.task_id}"

    # 检查是否刚刚点击过
    clicked = st.session_state.download_clicked.get(task.task_id, False)
    if clicked:
        st.session_state.download_clicked[task.task_id] = False
        # 显示即时反馈
        components.html("""
        <script>
            window.parent.postMessage({type: 'download_clicked'}, '*');
            if (typeof showDownloadFeedback === 'function') {
                showDownloadFeedback();
            }
        </script>
        """, height=0)

    # 下载按钮
    downloaded = st.download_button(
        label=f"📥 下载结果 ({file_size:.1f}KB)",
        data=task.result_data,
        file_name=f"optimized_{task.file_name}",
        mime="image/png",
        key=button_key,
        use_container_width=True,
        help="点击立即下载优化后的图片"
    )

    if downloaded:
        st.session_state.download_clicked[task.task_id] = True
        st.rerun()

# --- 9. 主界面 ---

def main():
    st.title("🎨 RunningHub AI - 智能图片优化工具")
    st.caption("本地并发处理 • 快速响应 • 实时更新")

    st.info(f"⏱️ 预计处理时间: {DISPLAY_TIMEOUT_MINUTES}分钟 | 🔄 刷新间隔: {AUTO_REFRESH_INTERVAL}秒 | 📊 最大并发: {MAX_CONCURRENT}")
    st.divider()

    # 主界面布局
    left_col, right_col = st.columns([1.8, 3.2])

    # 左侧：上传和状态
    with left_col:
        st.markdown("### 📁 文件上传")

        if st.session_state.upload_success:
            st.success("✅ 文件已添加到处理队列!")
            st.session_state.upload_success = False

        uploaded_files = st.file_uploader(
            "选择图片文件",
            type=['png', 'jpg', 'jpeg', 'webp'],
            accept_multiple_files=True,
            help="支持批量上传，自动加入处理队列",
            key=f"uploader_{st.session_state.file_uploader_key}"
        )

        if uploaded_files:
            with st.spinner(f'添加 {len(uploaded_files)} 个文件...'):
                for file in uploaded_files:
                    st.session_state.task_counter += 1
                    task = TaskItem(
                        st.session_state.task_counter, file.getvalue(), 
                        file.name, get_session_key()
                    )
                    st.session_state.tasks.append(task)
                    st.session_state.task_queue.append(task)

                st.session_state.upload_success = True
                st.session_state.file_uploader_key += 1
                st.rerun()

        st.divider()

        # 状态面板
        with st.expander("📊 系统状态", expanded=True):
            stats = get_stats()

            c1, c2, c3 = st.columns(3)

            with c1:
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#6f42c1">{stats["queued"]}</h4><p style="margin:0;font-size:11px">队列</p></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#28a745">{stats["success"]}</h4><p style="margin:0;font-size:11px">完成</p></div>', unsafe_allow_html=True)

            with c2:
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#fd7e14">{stats["processing"]}/{MAX_CONCURRENT}</h4><p style="margin:0;font-size:11px">处理中</p></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#dc3545">{stats["failed"]}</h4><p style="margin:0;font-size:11px">失败</p></div>', unsafe_allow_html=True)

            with c3:
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#6c757d">{stats["total"]}</h4><p style="margin:0;font-size:11px">总数</p></div>', unsafe_allow_html=True)
                st.markdown(f'<div class="metric-box"><h4 style="margin:0;color:#0066cc">{len(st.session_state.task_queue)}</h4><p style="margin:0;font-size:11px">等待</p></div>', unsafe_allow_html=True)

        # 系统信息
        with st.expander("⚙️ 系统信息", expanded=False):
            st.text(f"会话ID: {get_session_key()}")
            st.text(f"并发限制: {MAX_CONCURRENT}")
            st.text(f"总任务数: 无限制")

    # 右侧：任务列表
    with right_col:
        st.markdown("### 📋 任务列表")

        if not st.session_state.tasks:
            st.info("💡 暂无任务，请上传文件开始处理")
        else:
            start_new_tasks()

            # 显示任务
            for task in reversed(st.session_state.tasks):
                with st.container():
                    st.markdown('<div class="task-card">', unsafe_allow_html=True)

                    # 任务头部
                    col1, col2 = st.columns([4, 1])

                    with col1:
                        st.markdown(f"**{task.file_name}** `#{task.task_id}`")
                        if task.retry_count > 0:
                            st.markdown(f'<div class="compact-info">🔄 重试 {task.retry_count}/{MAX_RETRIES}</div>', unsafe_allow_html=True)

                    with col2:
                        if task.status == "SUCCESS":
                            st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                        elif task.status == "FAILED":
                            st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                        elif task.status == "PROCESSING":
                            st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                        else:
                            st.markdown('<span class="queued-badge">⏳ 队列中</span>', unsafe_allow_html=True)

                    # 进度和实时时间显示
                    if task.status == "PROCESSING":
                        st.progress(task.progress / 100, text=f"进度: {int(task.progress)}%")

                        if task.start_time:
                            st.markdown(f'''
                            <div class="compact-info real-time" 
                                 data-start-time="{task.start_time}" 
                                 data-display-timeout="{DISPLAY_TIMEOUT_MINUTES}">
                                ⏱️ 计算中...
                            </div>
                            ''', unsafe_allow_html=True)

                    elif task.status == "QUEUED":
                        st.markdown('<div class="compact-info">⏳ 等待处理...</div>', unsafe_allow_html=True)

                    # 结果处理
                    if task.status == "SUCCESS" and task.result_data:
                        elapsed_str = f"{int(task.elapsed_time//60)}:{int(task.elapsed_time%60):02d}"
                        st.success(f"🎉 处理完成! 用时: {elapsed_str}")
                        create_download_button(task)

                    elif task.status == "FAILED":
                        st.error(f"💥 处理失败")
                        if task.error_message:
                            st.markdown(f'<div class="compact-info">错误: {task.error_message}</div>', unsafe_allow_html=True)

                    st.markdown('</div>', unsafe_allow_html=True)

            st.divider()

            # 操作按钮
            col1, col2 = st.columns(2)

            with col1:
                if st.button("🗑️ 清空任务", use_container_width=True):
                    st.session_state.tasks = []
                    st.session_state.task_queue = []
                    st.session_state.download_clicked = {}
                    st.rerun()

            with col2:
                if st.button("🔄 重新启动队列", use_container_width=True):
                    # 将失败的任务重新加入队列
                    failed_tasks = [t for t in st.session_state.tasks if t.status == "FAILED"]
                    for task in failed_tasks:
                        task.status = "QUEUED"
                        task.retry_count = 0
                        task.error_message = None
                        task.progress = 0
                        st.session_state.task_queue.append(task)
                    st.success(f"✅ 已重启 {len(failed_tasks)} 个失败任务")
                    st.rerun()

    # 页脚
    st.divider()
    st.markdown("""
    <div style='text-align: center; color: #6c757d; padding: 15px;'>
        <b>🚀 RunningHub AI - 本地并发版</b><br>
        <small>5个并发限制 • 无总数限制 • 即时反馈</small>
    </div>
    """, unsafe_allow_html=True)

# --- 10. 应用入口 ---

if __name__ == "__main__":
    try:
        main()

        # 优化刷新逻辑
        has_active_tasks = any(t.status in ["PROCESSING", "QUEUED"] for t in st.session_state.tasks) or len(st.session_state.task_queue) > 0

        if has_active_tasks:
            time.sleep(AUTO_REFRESH_INTERVAL)
            st.rerun()

    except Exception as e:
        error_str = str(e).lower()
        if not any(kw in error_str for kw in ['websocket', 'tornado', 'streamlit']):
            st.error(f"⚠️ 系统错误: {str(e)[:100]}...")
            st.info("系统将自动恢复...")
            time.sleep(5)
        st.rerun()
