import streamlit as st
import requests
import time
import io
from PIL import Image
from datetime import datetime
import threading
import base64
import copy
import json
import random
import streamlit.components.v1 as components
from streamlit_server_state import server_state, server_state_lock

# 页面配置
st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义CSS样式
st.markdown("""
<style>
    .main {
        background-color: #f5f7fa;
    }
    .stButton>button {
        width: 100%;
        border-radius: 8px;
        height: 3em;
        background-color: #3498db;
        color: white;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #2980b9;
    }
    .task-card {
        background-color: white;
        border-radius: 10px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
    }
    .success-badge {
        color: #27ae60;
        font-weight: bold;
    }
    .error-badge {
        color: #e74c3c;
        font-weight: bold;
    }
    .processing-badge {
        color: #f39c12;
        font-weight: bold;
    }
    .info-badge {
        color: #17a2b8;
        font-weight: bold;
    }
    .waiting-badge {
        color: #9b59b6;
        font-weight: bold;
    }
    .queue-badge {
        color: #6c757d;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# 初始化全局服务器状态
with server_state_lock["global_processing_count"]:
    if "global_processing_count" not in server_state:
        server_state.global_processing_count = 0

with server_state_lock["global_task_queue"]:
    if "global_task_queue" not in server_state:
        server_state.global_task_queue = []

# 初始化 session_state
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
if 'session_id' not in st.session_state:
    st.session_state.session_id = f"session_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"

# 配置常量
MAX_GLOBAL_CONCURRENT = 5  # 全局最大并发数（对应API限制）
API_KEY = "c95f4c4d2703479abfbc55eefeb9bb71"
WEBAPP_ID = "1947599512657453057"
NODE_INFO = [
    {"nodeId": "38", "fieldName": "image", "fieldValue": "placeholder.png", "description": "图片输入"},
    {"nodeId": "60", "fieldName": "text", "fieldValue": "8k, high quality, high detail", "description": "正向提示词补充"},
    {"nodeId": "4", "fieldName": "text", "fieldValue": "色调艳丽,过曝,静态,细节模糊不清,字幕,风格,作品,画作,画面,静止,整体发灰,最差质量,低质量,JPEG压缩残留,丑陋的,残缺的,多余的手指,画得不好的手部,画得不好的脸部,畸形的,毁容的,形态畸形的肢体,手指融合,静止不动的画面,悲乱的背景,三条腿,背景人很多,倒着走", "description": "反向提示词"}
]

# API并发限制相关的错误关键词
CONCURRENT_LIMIT_ERRORS = [
    "concurrent limit",
    "too many requests", 
    "rate limit",
    "队列已满",
    "并发限制",
    "服务忙碌",
    "CONCURRENT_LIMIT_EXCEEDED",
    "TOO_MANY_REQUESTS"
]

class TaskItem:
    """任务项类"""
    def __init__(self, task_id, file_data, file_name, session_id):
        self.task_id = task_id
        self.session_id = session_id  # 添加会话ID
        self.file_data = file_data
        self.file_name = file_name
        self.status = "QUEUED"
        self.progress = 0
        self.result_url = None
        self.result_data = None
        self.error_message = None
        self.api_task_id = None
        self.created_at = datetime.now()
        self.start_time = None
        self.elapsed_time = None
        self.retry_count = 0
        self.max_retries = 10

def add_task_to_global_queue(task):
    """将任务添加到全局队列"""
    with server_state_lock["global_task_queue"]:
        # 创建任务的字典表示（因为server_state需要可序列化的对象）
        task_dict = {
            'task_id': task.task_id,
            'session_id': task.session_id,
            'file_name': task.file_name,
            'created_at': task.created_at.isoformat(),
            'status': task.status
        }
        server_state.global_task_queue.append(task_dict)

def get_next_task_from_queue():
    """从全局队列获取下一个要处理的任务"""
    with server_state_lock["global_task_queue"]:
        if server_state.global_task_queue:
            # 按创建时间排序，获取最早的任务
            server_state.global_task_queue.sort(key=lambda x: x['created_at'])
            return server_state.global_task_queue.pop(0)
    return None

def remove_task_from_queue(session_id, task_id):
    """从全局队列移除指定任务"""
    with server_state_lock["global_task_queue"]:
        server_state.global_task_queue = [
            t for t in server_state.global_task_queue 
            if not (t['session_id'] == session_id and t['task_id'] == task_id)
        ]

def increment_processing_count():
    """增加全局处理计数"""
    with server_state_lock["global_processing_count"]:
        server_state.global_processing_count += 1
        return server_state.global_processing_count

def decrement_processing_count():
    """减少全局处理计数"""
    with server_state_lock["global_processing_count"]:
        if server_state.global_processing_count > 0:
            server_state.global_processing_count -= 1
        return server_state.global_processing_count

def get_processing_count():
    """获取当前全局处理计数"""
    with server_state_lock["global_processing_count"]:
        return server_state.global_processing_count

def get_queue_position(session_id, task_id):
    """获取任务在全局队列中的位置"""
    with server_state_lock["global_task_queue"]:
        queue = sorted(server_state.global_task_queue, key=lambda x: x['created_at'])
        for i, task in enumerate(queue):
            if task['session_id'] == session_id and task['task_id'] == task_id:
                return i + 1
    return 0

def create_before_after_comparison(original_data, result_data, task_id):
    """创建原图与结果图的滑动对比组件"""
    # 将图片数据转换为base64
    original_b64 = base64.b64encode(original_data).decode()
    result_b64 = base64.b64encode(result_data).decode()
    
    html_code = f"""
    <div id="comparison-container-{task_id}" style="position: relative; width: 100%; max-width: 800px; margin: 0 auto; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
        <!-- 原图 (背景层) -->
        <img id="original-{task_id}" src="data:image/png;base64,{original_b64}" 
             style="width: 100%; height: auto; display: block;" alt="原图">
        
        <!-- 结果图 (遮罩层) -->
        <div id="result-overlay-{task_id}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; overflow: hidden;">
            <img id="result-{task_id}" src="data:image/png;base64,{result_b64}" 
                 style="width: 100%; height: 100%; object-fit: cover;" alt="优化后">
        </div>
        
        <!-- 分割线 -->
        <div id="divider-{task_id}" style="position: absolute; top: 0; width: 4px; height: 100%; background: linear-gradient(to bottom, #fff 0%, #3498db 50%, #fff 100%); cursor: ew-resize; z-index: 10; left: 50%; margin-left: -2px; box-shadow: 0 0 10px rgba(0,0,0,0.3);">
            <!-- 拖动手柄 -->
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 40px; height: 40px; background: #3498db; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.2);">
                <div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-top: 6px solid white; margin-right: 2px;"></div>
                <div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 6px solid white; margin-left: 2px;"></div>
            </div>
        </div>
        
        <!-- 标签 - 修正位置 -->
        <div style="position: absolute; top: 15px; right: 15px; background: rgba(0,0,0,0.7); color: white; padding: 5px 10px; border-radius: 15px; font-size: 12px; font-weight: bold;">
            原图
        </div>
        <div style="position: absolute; top: 15px; left: 15px; background: rgba(52, 152, 219, 0.9); color: white; padding: 5px 10px; border-radius: 15px; font-size: 12px; font-weight: bold;">
            AI优化
        </div>
        
        <!-- 下载按钮 -->
        <div id="download-btn-{task_id}" style="position: absolute; bottom: 15px; right: 15px; width: 50px; height: 50px; background: rgba(52, 152, 219, 0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.3); transition: all 0.3s ease;" 
             onmouseover="this.style.background='rgba(52, 152, 219, 1)'; this.style.transform='scale(1.1)'"
             onmouseout="this.style.background='rgba(52, 152, 219, 0.9)'; this.style.transform='scale(1)'">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="white">
                <path d="M14,2H6A2,2 0 0,0 4,4V20A2,2 0 0,0 6,22H18A2,2 0 0,0 20,20V8L14,2M18,20H6V4H13V9H18V20Z" />
                <path d="M12,11L16,15H13V19H11V15H8L12,11Z"/>
            </svg>
        </div>
    </div>

    <script>
    (function() {{
        const container = document.getElementById('comparison-container-{task_id}');
        const divider = document.getElementById('divider-{task_id}');
        const resultOverlay = document.getElementById('result-overlay-{task_id}');
        const downloadBtn = document.getElementById('download-btn-{task_id}');
        
        if (!container || !divider || !resultOverlay) return;
        
        let isDragging = false;
        let startX = 0;
        let startLeft = 0;
        
        function updateComparison(percentage) {{
            // 限制在 5% 到 95% 之间
            percentage = Math.max(5, Math.min(95, percentage));
            
            // 更新分割线位置
            divider.style.left = percentage + '%';
            
            // 更新结果图遮罩
            resultOverlay.style.clipPath = `inset(0 ${{100 - percentage}}% 0 0)`;
        }}
        
        function startDrag(e) {{
            isDragging = true;
            startX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
            
            const rect = container.getBoundingClientRect();
            const currentLeft = parseFloat(divider.style.left) || 50;
            startLeft = currentLeft;
            
            document.addEventListener(e.type.includes('touch') ? 'touchmove' : 'mousemove', handleDrag);
            document.addEventListener(e.type.includes('touch') ? 'touchend' : 'mouseup', stopDrag);
            
            e.preventDefault();
        }}
        
        function handleDrag(e) {{
            if (!isDragging) return;
            
            const currentX = e.type.includes('touch') ? e.touches[0].clientX : e.clientX;
            const rect = container.getBoundingClientRect();
            const deltaX = currentX - startX;
            const deltaPercentage = (deltaX / rect.width) * 100;
            const newPercentage = startLeft + deltaPercentage;
            
            updateComparison(newPercentage);
            e.preventDefault();
        }}
        
        function stopDrag() {{
            isDragging = false;
            document.removeEventListener('mousemove', handleDrag);
            document.removeEventListener('mouseup', stopDrag);
            document.removeEventListener('touchmove', handleDrag);
            document.removeEventListener('touchend', stopDrag);
        }}
        
        // 下载功能
        if (downloadBtn) {{
            downloadBtn.addEventListener('click', function(e) {{
                e.stopPropagation();
                
                // 创建下载链接
                const link = document.createElement('a');
                link.href = 'data:image/png;base64,{result_b64}';
                link.download = 'optimized_image.png';
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                // 显示下载提示
                const originalText = downloadBtn.innerHTML;
                downloadBtn.innerHTML = '<div style="color: white; font-size: 12px; font-weight: bold;">✓</div>';
                setTimeout(() => {{
                    downloadBtn.innerHTML = originalText;
                }}, 1000);
            }});
        }}
        
        // 初始化为显示结果图（70%）
        updateComparison(70);
        
        // 绑定事件
        divider.addEventListener('mousedown', startDrag);
        divider.addEventListener('touchstart', startDrag);
        
        // 点击容器其他位置也可以调整
        container.addEventListener('click', function(e) {{
            if (e.target === divider || divider.contains(e.target) || e.target === downloadBtn || downloadBtn.contains(e.target)) return;
            
            const rect = container.getBoundingClientRect();
            const clickX = e.clientX - rect.left;
            const percentage = (clickX / rect.width) * 100;
            updateComparison(percentage);
        }});
    }})();
    </script>
    """
    
    return html_code

def is_concurrent_limit_error(error_msg):
    """检查是否是并发限制错误"""
    error_msg_lower = error_msg.lower()
    return any(keyword in error_msg_lower for keyword in CONCURRENT_LIMIT_ERRORS)

def upload_file(file_data, file_name, api_key):
    """上传文件到服务器"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    
    response_data = response.json()
    
    if response_data.get("code") == 0:
        uploaded_filename = response_data['data'] ['fileName']
        return uploaded_filename
    else:
        error_msg = f"图片上传失败: {response_data.get('msg', '未知错误')}"
        raise Exception(error_msg)

def run_task(api_key, webapp_id, node_info_list):
    """发起任务"""
    run_url = 'https://www.runninghub.cn/task/openapi/ai-app/run'
    headers = {'Content-Type': 'application/json'}
    payload = {
        "apiKey": api_key,
        "webappId": webapp_id,
        "nodeInfoList": node_info_list
    }
    
    response = requests.post(run_url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    run_data = response.json()
    
    if run_data.get("code") != 0:
        error_msg = f"发起任务失败: {run_data.get('msg', '未知错误')}"
        raise Exception(error_msg)
    
    task_id = run_data['data'] ['taskId']
    return task_id

def fetch_task_output(api_key, task_id):
    """获取任务输出"""
    output_url = 'https://www.runninghub.cn/task/openapi/outputs'
    
    response = requests.post(output_url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"] [0].get("fileUrl")
        if file_url:
            return file_url
        else:
            raise Exception("未找到图片URL")
    else:
        raise Exception(f"获取结果失败: {data.get('msg', '未知错误')}")

def download_result_image(url):
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    content = response.content
    return content

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务"""
    try:
        # 增加全局处理计数
        current_count = increment_processing_count()
        
        task.status = "UPLOADING"
        task.start_time = time.time()
        task.progress = 5
        
        # 步骤1: 上传文件
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        task.progress = 15
        
        # 步骤2: 准备节点信息
        node_info_list = copy.deepcopy(node_info)
        
        # 更新图片节点
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 步骤3: 发起任务
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        task.status = "PROCESSING"
        task.progress = 20
        
        # 步骤4: 轮询状态
        progress = 20
        max_polls = 60
        poll_count = 0
        status = None
        
        while poll_count < max_polls:
            time.sleep(3)
            poll_count += 1
            
            status_url = 'https://www.runninghub.cn/task/openapi/status'
            response = requests.post(status_url, json={'apiKey': api_key, 'taskId': task.api_task_id}, timeout=10)
            response.raise_for_status()
            data = response.json()
            status = data.get('data')
            
            if progress < 95:
                progress += min(2, (95 - progress) / 10)
                progress = int(progress)
            
            task.progress = progress
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("任务处理失败")
            elif status in ["QUEUED", "RUNNING"]:
                continue
            else:
                continue
        
        if poll_count >= max_polls:
            raise Exception("任务处理超时")
        
        if status == "SUCCESS":
            task.progress = 95
            result_url = fetch_task_output(api_key, task.api_task_id)
            task.result_url = result_url
            
            task.result_data = download_result_image(result_url)
            task.progress = 100
            task.status = "SUCCESS"
            task.elapsed_time = time.time() - task.start_time
        else:
            raise Exception(f"任务未成功完成，最终状态: {status}")
            
    except Exception as e:
        error_msg = str(e)
        
        if is_concurrent_limit_error(error_msg) and task.retry_count < task.max_retries:
            task.status = "WAITING"
            task.retry_count += 1
            task.progress = 0
            # 重新加入全局队列
            add_task_to_global_queue(task)
            wait_time = random.randint(2, 10)
            time.sleep(wait_time)
            task.status = "QUEUED"
        else:
            task.status = "FAILED"
            task.error_message = error_msg
            task.elapsed_time = time.time() - task.start_time if task.start_time else 0
    
    finally:
        # 减少全局处理计数
        decrement_processing_count()

# 主界面
st.title("🎨 RunningHub AI - 智能图片优化工具")
st.markdown("### 专业的AI图片优化和增强服务")

# 统计信息
col1, col2, col3, col4, col5 = st.columns(5)

# 获取全局统计
global_processing = get_processing_count()
with server_state_lock["global_task_queue"]:
    global_queue_size = len(server_state.global_task_queue)

# 本地统计
local_queued = sum(1 for t in st.session_state.tasks if t.status == "QUEUED")
local_processing = sum(1 for t in st.session_state.tasks if t.status in ["UPLOADING", "PROCESSING"])
local_waiting = sum(1 for t in st.session_state.tasks if t.status == "WAITING")
local_completed = sum(1 for t in st.session_state.tasks if t.status == "SUCCESS")
local_failed = sum(1 for t in st.session_state.tasks if t.status == "FAILED")

with col1:
    st.metric("全局队列", global_queue_size)
with col2:
    st.metric("全局处理中", f"{global_processing}/{MAX_GLOBAL_CONCURRENT}")
with col3:
    st.metric("等待重试", local_waiting)
with col4:
    st.metric("已完成", local_completed)
with col5:
    st.metric("失败", local_failed)

st.markdown("---")

# 左右分栏
left_col, right_col = st.columns([2, 3])

with left_col:
    st.markdown("### 📁 图片上传")
    
    uploaded_files = st.file_uploader(
        "选择图片文件（支持多选）",
        type=['png', 'jpg', 'jpeg', 'webp'],
        accept_multiple_files=True,
        help="可以一次选择多张图片进行批量处理，上传后自动加入全局处理队列",
        key=f"file_uploader_{st.session_state.file_uploader_key}"
    )
    
    if uploaded_files:
        for uploaded_file in uploaded_files:
            st.session_state.task_counter += 1
            task = TaskItem(
                task_id=st.session_state.task_counter,
                file_data=uploaded_file.getvalue(),
                file_name=uploaded_file.name,
                session_id=st.session_state.session_id
            )
            st.session_state.tasks.append(task)
            # 添加到全局队列
            add_task_to_global_queue(task)
        
        st.success(f"已添加 {len(uploaded_files)} 个任务到全局队列！")
        st.session_state.file_uploader_key += 1
        st.rerun()
    
    st.markdown("---")
    
    # 队列状态说明
    with st.expander("📊 队列状态说明", expanded=False):
        st.markdown("""
        - **全局队列**: 所有用户的等待任务总数
        - **全局处理中**: 当前正在处理的任务数/最大并发数
        - **等待重试**: API繁忙，排队等待重试
        - **已完成**: 处理成功
        - **失败**: 处理失败（超过重试次数）
        
        **新的全局并发控制机制：**
        - 所有用户共享最多5个并发处理槽位
        - 超过限制的任务自动进入全局队列等待
        - 智能重试机制，API繁忙时自动排队
        """)
    
    with st.expander("⚙️ API 配置信息", expanded=False):
        st.text_input("API Key", value=API_KEY, disabled=True)
        st.text_input("WebApp ID", value=WEBAPP_ID, disabled=True)
        st.markdown(f"**全局并发限制**: {MAX_GLOBAL_CONCURRENT}")
        st.markdown("**节点信息配置：**")
        st.json(NODE_INFO)

with right_col:
    st.markdown("### 📊 任务队列")
    
    if not st.session_state.tasks:
        st.info("暂无任务，请上传图片开始处理")
    else:
        # 全局并发控制逻辑
        current_global_processing = get_processing_count()
        
        # 检查是否有任务可以从队列开始处理
        for task in st.session_state.tasks:
            if (task.status == "QUEUED" and 
                current_global_processing < MAX_GLOBAL_CONCURRENT):
                
                # 从全局队列中获取下一个任务
                next_task = get_next_task_from_queue()
                if (next_task and 
                    next_task['session_id'] == st.session_state.session_id and 
                    next_task['task_id'] == task.task_id):
                    
                    # 启动任务处理
                    thread = threading.Thread(
                        target=process_single_task,
                        args=(task, API_KEY, WEBAPP_ID, NODE_INFO)
                    )
                    thread.daemon = True
                    thread.start()
                    current_global_processing += 1
        
        # 显示所有任务
        for task in reversed(st.session_state.tasks):
            with st.container():
                st.markdown(f'<div class="task-card">', unsafe_allow_html=True)
                
                col_title, col_status = st.columns([3, 1])
                with col_title:
                    st.markdown(f"**📄 {task.file_name}** (Task-{task.task_id})")
                    if task.retry_count > 0:
                        st.caption(f"重试次数: {task.retry_count}/{task.max_retries}")
                    
                    # 显示队列位置
                    if task.status == "QUEUED":
                        position = get_queue_position(st.session_state.session_id, task.task_id)
                        if position > 0:
                            st.caption(f"全局队列位置: 第 {position} 位")
                
                with col_status:
                    if task.status == "SUCCESS":
                        st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                    elif task.status == "FAILED":
                        st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                    elif task.status in ["UPLOADING", "PROCESSING"]:
                        st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                    elif task.status == "WAITING":
                        st.markdown('<span class="waiting-badge">⏳ 等待重试</span>', unsafe_allow_html=True)
                    else:
                        st.markdown('<span class="queue-badge">⏸️ 排队中</span>', unsafe_allow_html=True)
                
                # 进度条
                if task.status in ["UPLOADING", "PROCESSING"]:
                    st.progress(task.progress / 100)
                    st.caption(f"进度: {task.progress}%")
                    
                    if task.start_time:
                        elapsed = time.time() - task.start_time
                        remaining = max(0, 180 - elapsed)
                        minutes = int(remaining // 60)
                        seconds = int(remaining % 60)
                        st.caption(f"剩余时间: 约{minutes}分{seconds}秒")
                elif task.status == "WAITING":
                    st.info("API服务繁忙，正在等待重试...")
                elif task.status == "QUEUED":
                    st.info(f"正在全局队列中等待处理... (当前全局并发: {current_global_processing}/{MAX_GLOBAL_CONCURRENT})")
                
                # 结果显示
                if task.status == "SUCCESS" and task.result_data:
                    elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                    st.success(f"✅ 处理完成！用时: {elapsed_str}")
                    
                    st.markdown("**🔍 原图 vs AI优化对比**（拖动中间线或点击任意位置对比，点击右下角图标下载）")
                    comparison_html = create_before_after_comparison(task.file_data, task.result_data, task.task_id)
                    components.html(comparison_html, height=600)
                    
                    st.caption("💡 左侧显示AI优化效果，右侧显示原图。拖动中间线或点击图片任意位置进行对比。")
                
                elif task.status == "FAILED":
                    st.error(f"❌ 处理失败: {task.error_message}")
                    if task.retry_count >= task.max_retries:
                        st.warning("已达到最大重试次数")
                
                st.markdown('</div>', unsafe_allow_html=True)
                st.markdown("---")
        
        if st.button("🗑️ 清空所有任务"):
            # 从全局队列中移除本会话的所有任务
            for task in st.session_state.tasks:
                remove_task_from_queue(st.session_state.session_id, task.task_id)
            st.session_state.tasks = []
            st.rerun()

# 页脚
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #7f8c8d;'>
    <p>🚀 <strong>全局并发控制</strong>：最多5个全局并发任务，多用户共享处理资源</p>
    <p>📤 上传文件后自动加入全局处理队列，智能排队机制确保公平处理</p>
    <p>🔍 完成后支持原图与AI优化图片的滑动对比预览，点击图片右下角图标直接下载</p>
</div>
""", unsafe_allow_html=True)

# 自动刷新
if any(t.status in ["UPLOADING", "PROCESSING", "WAITING", "QUEUED"] for t in st.session_state.tasks):
    time.sleep(2)
    st.rerun()
