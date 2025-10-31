import streamlit as st
import requests
import time
import io
from PIL import Image  # 确保 PIL (Pillow) 已安装
from datetime import datetime
import threading
import base64
import copy
import json
import random
import streamlit.components.v1 as components
import redis 
import logging

# --- 1. 全局配置和Redis初始化 ---

# 页面配置
st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具 (分布式)",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 配置日志以减少WebSocket错误噪音
logging.getLogger("tornado.access").setLevel(logging.WARNING)
logging.getLogger("tornado.application").setLevel(logging.WARNING)
logging.getLogger("tornado.general").setLevel(logging.WARNING)

# Redis 配置 (使用您的连接信息)
REDIS_HOST = 'redis-18743.c340.ap-northeast-2-1.ec2.redns.redis-cloud.com'
REDIS_PORT = 18743
REDIS_PASSWORD = "dBAPubXYReEwHaIvnvX0lvr3qIgtudCp"

# 初始化 Redis 连接 (仅在 session_state 中未连接时尝试)
if 'redis_connected' not in st.session_state:
    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            username="default",
            password=REDIS_PASSWORD,
            socket_timeout=5
        )
        r.ping()
        st.session_state.r = r
        st.session_state.redis_connected = True
        st.session_state.redis_error = None
    except Exception as e:
        st.session_state.redis_connected = False
        st.session_state.redis_error = f"Redis连接失败: {e}"
        st.session_state.r = None
else:
    r = st.session_state.r # 从 session_state 获取已连接的实例

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
</style>
""", unsafe_allow_html=True)

# 初始化 session_state
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
# 缓存已完成任务的HTML，避免重复加载Base64数据
if 'completed_html_cache' not in st.session_state:
    st.session_state.completed_html_cache = {}
# 添加错误处理状态
if 'last_error_time' not in st.session_state:
    st.session_state.last_error_time = 0
if 'error_count' not in st.session_state:
    st.session_state.error_count = 0

# 配置常量
MAX_LOCAL_CONCURRENT = 5  # 本地最大并发数（已不再重要，但保留）
MAX_GLOBAL_CONCURRENT = 5 # RunningHub API的最大并发数（核心限制）
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

# Redis 键名
GLOBAL_TASK_QUEUE = "runninghub:task_queue"
GLOBAL_PROCESSING_SET = "runninghub:processing_tasks"

# --- 2. TaskItem 类和序列化助手函数 ---

class TaskItem:
    """任务项类"""
    def __init__(self, task_id, file_data, file_name):
        self.task_id = task_id
        self.file_data = file_data # 原始文件数据 (仅保存在本地内存)
        self.file_name = file_name
        self.status = "PENDING_QUEUE" # 新状态：待推入全局队列
        self.progress = 0
        self.result_url = None
        self.result_data = None
        self.error_message = None
        self.api_task_id = None
        self.created_at = datetime.now()
        self.start_time = None
        self.elapsed_time = None
        self.retry_count = 0  # 重试次数
        self.max_retries = 3  # 降低重试次数，避免占用资源过久

def serialize_task_data(task: TaskItem):
    """将任务元数据序列化为JSON字符串，以便存储到Redis队列"""
    data = {
        'task_id': task.task_id,
        'file_name': task.file_name,
        'created_at': task.created_at.isoformat(),
        'retry_count': task.retry_count,
        'max_retries': task.max_retries,
        # ⚠️ 注意：file_data 不被序列化
    }
    return json.dumps(data)

def update_task_from_redis_data(task: TaskItem, data: dict):
    """从Redis中取出的数据更新本地任务实例"""
    task.retry_count = data['retry_count']
    task.max_retries = data['max_retries']
    task.created_at = datetime.fromisoformat(data['created_at'])
    task.status = "QUEUED" # 标记为已被调度器取出，正在排队等待线程启动

# --- 3. 辅助函数 (WebP 优化版本) ---

def create_before_after_comparison(original_data, result_data, task_id):
    """
    创建原图与结果图的滑动对比组件
    (优化：使用WebP进行显示加速，但保留原始PNG/JPG用于下载)
    """
    
    display_format = "webp"
    download_format = "png" # 假设API返回的是PNG，与原代码下载逻辑一致
    
    # --- 1. 转换为 WebP (用于显示) ---
    def to_webp_b64(img_bytes, quality=80):
        """将原始图片字节转换为用于显示的WebP Base64"""
        img = Image.open(io.BytesIO(img_bytes))
        buffer = io.BytesIO()
        # 使用较低质量(80)的WebP来最大化压缩，加快前端加载
        img.save(buffer, format="WEBP", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode()

    try:
        # 转换用于显示的图片 (display images)
        original_b64_display = to_webp_b64(original_data, quality=80)
        result_b64_display = to_webp_b64(result_data, quality=80)
        
    except Exception as e:
        # Fallback: 如果WebP转换失败，则回退到使用原始编码 (速度会变慢)
        st.warning(f"任务 {task_id} 的WebP转换失败 ({e})。将回退到PNG显示(可能较慢)。")
        original_b64_display = base64.b64encode(original_data).decode()
        result_b64_display = base64.b64encode(result_data).decode()
        display_format = "png" # 回退到PNG格式

    # --- 2. 准备原始结果 (用于下载) ---
    # 下载按钮应提供API返回的、未经压缩的原始优化结果
    # 我们需要原始结果的 Base64
    result_b64_download = base64.b64encode(result_data).decode()

    # --- 3. 生成 HTML ---
    html_code = f"""
    <div id="comparison-container-{task_id}" style="position: relative; width: 100%; max-width: 800px; margin: 0 auto; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
        
        <img id="original-{task_id}" src="data:image/{display_format};base64,{original_b64_display}" 
             style="width: 100%; height: auto; display: block;" alt="原图">
        
        <div id="result-overlay-{task_id}" style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; overflow: hidden;">
            <img id="result-{task_id}" src="data:image/{display_format};base64,{result_b64_display}" 
                 style="width: 100%; height: 100%; object-fit: cover;" alt="优化后">
        </div>
        
        <div id="divider-{task_id}" style="position: absolute; top: 0; width: 4px; height: 100%; background: linear-gradient(to bottom, #fff 0%, #3498db 50%, #fff 100%); cursor: ew-resize; z-index: 10; left: 50%; margin-left: -2px; box-shadow: 0 0 10px rgba(0,0,0,0.3);">
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 40px; height: 40px; background: #3498db; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 8px rgba(0,0,0,0.2);">
                <div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-top: 6px solid white; margin-right: 2px;"></div>
                <div style="width: 0; height: 0; border-left: 8px solid transparent; border-right: 8px solid transparent; border-bottom: 6px solid white; margin-left: 2px;"></div>
            </div>
        </div>
        
        <div style="position: absolute; top: 15px; right: 15px; background: rgba(0,0,0,0.7); color: white; padding: 5px 10px; border-radius: 15px; font-size: 12px; font-weight: bold; z-index: 100;">
            原图
        </div>
        <div style="position: absolute; top: 15px; left: 15px; background: rgba(52, 152, 219, 0.9); color: white; padding: 5px 10px; border-radius: 15px; font-size: 12px; font-weight: bold; z-index: 100;">
            AI优化
        </div>
        
        <div id="download-btn-{task_id}" style="position: absolute; bottom: 15px; right: 15px; width: 50px; height: 50px; background: rgba(52, 152, 219, 0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.3); transition: all 0.3s ease; z-index: 100;" 
             onmouseover="this.style.background='rgba(52, 152, 219, 1)'; this.style.transform='scale(1.1)'"
             onmouseout="this.style.background='rgba(52, 152, 219, 0.9)'; this.style.transform='scale(1)'">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="white">
                <path d="M19 9h-4V3H9v6H5l7 7 7-7zM5 18v2h14v-2H5z"/>
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
                
                // 优化：这里使用原始的、高质量的下载数据 (result_b64_download)
                // 和 对应的下载格式 (download_format)
                link.href = 'data:image/{download_format};base64,{result_b64_download}';
                link.download = 'optimized_{task_id}.{download_format}';
                
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                // 显示下载提示
                const originalSvg = downloadBtn.innerHTML;
                downloadBtn.innerHTML = '<div style="color: white; font-size: 18px; font-weight: bold;">✓</div>';
                setTimeout(() => {{
                    downloadBtn.innerHTML = originalSvg;
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
            // 确保点击的不是下载按钮或手柄
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
    url = 'https://www.runninghub.cn/task/openapi/upload'
    
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    
    response_data = response.json()
    
    if response_data.get("code") == 0:
        uploaded_filename = response_data['data']['fileName']
        return uploaded_filename
    else:
        error_msg = f"图片上传失败: {response_data.get('msg', '未知错误')}"
        raise Exception(error_msg)

def run_task(api_key, webapp_id, node_info_list):
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
    
    task_id = run_data['data']['taskId']
    return task_id

def fetch_task_output(api_key, task_id):
    output_url = 'https://www.runninghub.cn/task/openapi/outputs'
    
    response = requests.post(output_url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"][0].get("fileUrl")
        if file_url:
            return file_url
        else:
            raise Exception("未找到图片URL")
    else:
        raise Exception(f"获取结果失败: {data.get('msg', '未知错误')}")

def download_result_image(url):
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    content = response.content
    return content

# --- 4. 任务处理逻辑修改 (移除 session_state 访问) ---

def process_single_task(task: TaskItem, api_key, webapp_id, node_info, r, processing_set_key, global_queue_key):
    """
    处理单个任务（已获得全局许可）。
    会在Redis的全局处理集合中注册和注销任务。
    """
    task_id_str = str(task.task_id)
    
    try:
        task.status = "UPLOADING"
        task.start_time = time.time()
        task.progress = 5
        
        # 注册到全局处理集合
        r.sadd(processing_set_key, task_id_str)
        
        # 步骤1: 上传文件
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        task.progress = 15
        
        # 步骤2: 准备节点信息
        node_info_list = copy.deepcopy(node_info)
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 步骤3: 发起任务
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        task.status = "PROCESSING"
        task.progress = 20
        
        # 步骤4: 轮询状态
        progress = 20
        max_polls = 60  # 最多轮询60次（约3分钟）
        poll_count = 0
        status = None
        
        while poll_count < max_polls:
            time.sleep(3)  # 每3秒轮询一次
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
            # 持续等待
            
        # 检查是否超时
        if poll_count >= max_polls:
            raise Exception("任务处理超时")
        
        # 只有在状态为SUCCESS时才获取结果
        if status == "SUCCESS":
            # 步骤5: 获取结果
            task.progress = 95
            result_url = fetch_task_output(api_key, task.api_task_id)
            task.result_url = result_url
            
            # 步骤6: 下载结果
            task.result_data = download_result_image(result_url)
            task.progress = 100
            task.status = "SUCCESS"
            task.elapsed_time = time.time() - task.start_time

        else:
            raise Exception(f"任务未成功完成，最终状态: {status}")
            
    except Exception as e:
        error_msg = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0
        
        # 检查是否是并发限制错误
        if is_concurrent_limit_error(error_msg) and task.retry_count < task.max_retries:
            # 遇到并发限制错误：递增重试计数，并将其重新推入全局队列
            task.retry_count += 1
            task.status = "WAITING" # 本地显示等待重试
            task.error_message = f"API并发限制，第{task.retry_count}次重试..."
            
            # 重新序列化任务信息并推回全局队列尾部
            task_data_json = serialize_task_data(task)
            r.rpush(global_queue_key, task_data_json)
            
        else:
            # 其他错误或超过最大重试次数
            task.status = "FAILED"
            task.error_message = error_msg
            if task.retry_count >= task.max_retries:
                 task.error_message += " (达到最大重试次数)"
            
    finally:
        # 无论成功、失败或重新入队，都从全局处理集合中移除
        r.srem(processing_set_key, task_id_str)

# --- 5. 错误处理和安全刷新函数 ---

def safe_rerun():
    """安全的页面刷新函数，包含错误处理"""
    try:
        st.rerun()
    except Exception as e:
        error_str = str(e).lower()
        current_time = time.time()
        
        # 检查是否是WebSocket相关错误
        if any(keyword in error_str for keyword in ['websocketclosederror', 'streamclosederror', 'tornado']):
            # 记录错误但不显示给用户
            st.session_state.error_count += 1
            st.session_state.last_error_time = current_time
            
            # 如果错误过于频繁，暂时停止刷新
            if st.session_state.error_count > 10:
                if current_time - st.session_state.last_error_time > 60:  # 1分钟后重置
                    st.session_state.error_count = 0
                else:
                    return  # 暂时停止刷新
        else:
            # 非WebSocket错误，显示给用户
            st.error(f"页面刷新时发生错误: {e}")

def should_auto_refresh():
    """判断是否应该自动刷新"""
    if not st.session_state.redis_connected:
        return False
        
    # 检查是否有需要刷新的条件
    has_active_tasks = any(t.status in ["UPLOADING", "PROCESSING", "WAITING", "QUEUED"] for t in st.session_state.tasks)
    
    try:
        has_global_queue = r.llen(GLOBAL_TASK_QUEUE) > 0
        has_global_processing = r.scard(GLOBAL_PROCESSING_SET) > 0
    except:
        has_global_queue = False
        has_global_processing = False
        
    return has_active_tasks or has_global_queue or has_global_processing

# --- 6. 主界面 (增加主线程缓存逻辑) ---

def main():
    st.title("🎨 RunningHub AI - 智能图片优化工具 (分布式队列)")

    # 左右分栏
    left_col, right_col = st.columns([2, 3])

    with left_col:
        st.markdown("### 📁 图片上传")
        
        uploaded_files = st.file_uploader(
            "选择图片文件（支持多选）",
            type=['png', 'jpg', 'jpeg', 'webp'],
            accept_multiple_files=True,
            help="上传后自动加入全局处理队列，等待任意空闲机器调度。",
            key=f"file_uploader_{st.session_state.file_uploader_key}"
        )
        
        # 自动加入队列逻辑 (推入 Redis 队列)
        if uploaded_files:
            if not st.session_state.redis_connected:
                st.error("无法连接到Redis，请检查配置或稍后再试。")
            else:
                new_tasks = []
                for uploaded_file in uploaded_files:
                    st.session_state.task_counter += 1
                    task = TaskItem(
                        task_id=st.session_state.task_counter,
                        file_data=uploaded_file.getvalue(),
                        file_name=uploaded_file.name
                    )
                    st.session_state.tasks.append(task)
                    new_tasks.append(task)
                    
                if new_tasks:
                    try:
                        pipe = r.pipeline()
                        for task in new_tasks:
                            task_data_json = serialize_task_data(task)
                            pipe.rpush(GLOBAL_TASK_QUEUE, task_data_json) # 尾部插入
                        pipe.execute()
                        
                        st.success(f"已添加 {len(uploaded_files)} 个任务到**全局队列**！")
                        
                        st.session_state.file_uploader_key += 1
                        safe_rerun()
                    except Exception as e:
                        st.error(f"添加任务到队列失败: {e}")
        
        st.markdown("---")
        
        # 全局状态与API配置信息
        with st.expander("📊 全局状态与API配置信息", expanded=False):
            
            # 状态信息
            if st.session_state.redis_connected:
                st.markdown(f"**Redis状态:** ✅ 连接成功 | 全局并发限制: **{MAX_GLOBAL_CONCURRENT}**")
                
                try:
                    global_queued = r.llen(GLOBAL_TASK_QUEUE)
                    global_processing = r.scard(GLOBAL_PROCESSING_SET)
                    local_completed = sum(1 for t in st.session_state.tasks if t.status == "SUCCESS")
                    local_failed = sum(1 for t in st.session_state.tasks if t.status == "FAILED")
                    local_waiting_retry = sum(1 for t in st.session_state.tasks if t.status == "WAITING")
                    total_submitted = len(st.session_state.tasks)

                    st.markdown("""
                    | 状态指标 | 数值 |
                    | :--- | :--- |
                    | **全局队列中** | {} |
                    | **全局处理中** | {} / {} |
                    | **本次提交总数** | {} |
                    | **已完成 (本地)** | {} |
                    | **本地失败/重试** | {} / {} |
                    """.format(global_queued, global_processing, MAX_GLOBAL_CONCURRENT, total_submitted, local_completed, local_failed, local_waiting_retry))
                except Exception as e:
                    st.warning(f"获取Redis状态时出错: {e}")
                
            else:
                st.error(f"❌ Redis连接失败，全局排队系统不可用。错误: {st.session_state.redis_error}")
            
            st.markdown("---")
            st.markdown("**API配置信息：**")
            st.text_input("API Key", value=API_KEY, disabled=True)
            st.text_input("WebApp ID", value=WEBAPP_ID, disabled=True)
            st.markdown("**Redis连接信息：**")
            st.json({
                "Host": REDIS_HOST,
                "Port": REDIS_PORT,
                "Queue Key": GLOBAL_TASK_QUEUE,
                "Processing Key": GLOBAL_PROCESSING_SET
            })
            st.markdown("**节点信息配置：**")
            st.json(NODE_INFO)

    with right_col:
        st.markdown("### 📊 任务队列 (本地视图)")
        
        if not st.session_state.tasks:
            st.info("暂无任务，请上传图片开始处理")
        else:
            # 核心：全局调度器
            if st.session_state.redis_connected:
                try:
                    global_processing_count = r.scard(GLOBAL_PROCESSING_SET)
                    available_slots = MAX_GLOBAL_CONCURRENT - global_processing_count
                    
                    # 从全局任务队列拉取任务
                    if available_slots > 0:
                        for _ in range(available_slots):
                            task_json = r.lpop(GLOBAL_TASK_QUEUE)
                            
                            if task_json:
                                task_data = json.loads(task_json)
                                task_id = task_data['task_id']
                                
                                local_task = next((t for t in st.session_state.tasks if t.task_id == task_id), None)
                                
                                if local_task and local_task.file_data:
                                    update_task_from_redis_data(local_task, task_data) 
                                    
                                    # 立即将任务ID加入全局处理集合，占据槽位
                                    r.sadd(GLOBAL_PROCESSING_SET, str(local_task.task_id))
                                    
                                    thread = threading.Thread(
                                        target=process_single_task,
                                        args=(local_task, API_KEY, WEBAPP_ID, NODE_INFO, r, GLOBAL_PROCESSING_SET, GLOBAL_TASK_QUEUE)
                                    )
                                    thread.daemon = True
                                    thread.start()
                                    
                                    local_task.status = "UPLOADING" 
                                    
                                else:
                                    # 任务ID不在本地，重新放回队列让其他机器处理
                                    if task_data.get('retry_count', 0) > 0:
                                        r.rpush(GLOBAL_TASK_QUEUE, task_json)
                except Exception as e:
                    st.error(f"调度器运行时出错: {e}")
            
            # 显示所有任务
            for task in reversed(st.session_state.tasks):
                with st.container():
                    st.markdown(f'<div class="task-card">', unsafe_allow_html=True)
                    
                    col_title, col_status = st.columns([3, 1])
                    with col_title:
                        st.markdown(f"**📄 {task.file_name}** (Task-{task.task_id})")
                        if task.retry_count > 0:
                            st.caption(f"重试次数: {task.retry_count}/{task.max_retries}")
                    with col_status:
                        if task.status == "SUCCESS":
                            st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                        elif task.status == "FAILED":
                            st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                        elif task.status in ["UPLOADING", "PROCESSING"]:
                            st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                        elif task.status == "WAITING":
                            st.markdown('<span class="waiting-badge">⏳ 等待重试/重新排队</span>', unsafe_allow_html=True) 
                        elif task.status == "QUEUED":
                            st.markdown('<span class="info-badge">⏸️ 已被调度，等待线程启动</span>', unsafe_allow_html=True)
                        else:
                             st.markdown('<span class="info-badge">📦 待推入全局队列</span>', unsafe_allow_html=True)
                    
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
                        st.info(task.error_message)
                    
                    # 结果显示 - 检查是否需要生成和缓存 HTML
                    if task.status == "SUCCESS":
                        elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                        st.success(f"✅ 处理完成！用时: {elapsed_str}")
                        
                        # 检查缓存中是否已有，如果没有，则在主线程中生成并缓存
                        if task.task_id not in st.session_state.completed_html_cache:
                            try:
                                # 确保数据都存在
                                if task.file_data and task.result_data:
                                    # 使用优化后的 create_before_after_comparison 函数
                                    st.session_state.completed_html_cache[task.task_id] = create_before_after_comparison(task.file_data, task.result_data, task.task_id)
                                else:
                                    st.warning("任务数据不完整，无法生成对比图。")
                            except Exception as e:
                                st.error(f"生成对比图时出错: {e}")
                        
                        # 现在可以安全地从缓存中读取
                        if task.task_id in st.session_state.completed_html_cache:
                            st.markdown("**🔍 原图 vs AI优化对比**（拖动中间线或点击任意位置对比，点击右下角图标下载）")
                            # 使用缓存的 HTML，避免重复 Base64 加载
                            components.html(st.session_state.completed_html_cache[task.task_id], height=600)
                            st.caption("💡 左侧显示AI优化效果，右侧显示原图。拖动中间线或点击图片任意位置进行对比。")
                    
                    elif task.status == "FAILED":
                        st.error(f"❌ 最终失败: {task.error_message}")
                    
                    st.markdown('</div>', unsafe_allow_html=True)
                    st.markdown("---")
            
            # 清空按钮
            col_local, col_global = st.columns(2)
            with col_local:
                if st.button("🗑️ 清空本地任务"):
                    st.session_state.tasks = []
                    st.session_state.completed_html_cache = {}
                    safe_rerun()
            with col_global:
                if st.button("🔥 清空全局队列和处理中的任务", help="**危险操作**：会停止所有机器上正在处理的任务，并清空所有排队任务。"):
                    try:
                        r.delete(GLOBAL_TASK_QUEUE)
                        r.delete(GLOBAL_PROCESSING_SET)
                        st.session_state.tasks = []
                        st.session_state.completed_html_cache = {}
                        st.success("已清空全局队列和处理中的任务！")
                        safe_rerun()
                    except Exception as e:
                        st.error(f"清空全局队列时出错: {e}")

    # 页脚
    st.markdown("---")
    st.markdown("""
    <div style='text-align: center; color: #7f8c8d;'>
        <p>🚀 基于Redis实现分布式限流，**全局并发限制在5**，多机器提交任务自动排队</p>
        <p>📤 上传文件后，任务进入Redis全局队列，由任一空闲机器调度处理</p>
        <p>⚡️ **性能优化**：对比图使用 WebP 加速加载，同时保留高质量下载</p>
        <p>🔧 **已优化**：修复了WebSocket连接错误，改善了页面稳定性</p>
    </div>
    """, unsafe_allow_html=True)

# --- 7. 应用入口点和自动刷新逻辑 ---

if __name__ == "__main__":
    try:
        main()
        
        # 自动刷新逻辑（使用安全刷新）
        if should_auto_refresh():
            time.sleep(2)
            safe_rerun()
            
    except Exception as e:
        error_str = str(e).lower()
        if any(keyword in error_str for keyword in ['websocketclosederror', 'streamclosederror', 'tornado']):
            # WebSocket错误，静默处理
            st.session_state.error_count = st.session_state.get('error_count', 0) + 1
        else:
            # 其他错误，显示给用户
            st.error(f"应用运行时发生错误: {e}")
            st.info("页面将在几秒后自动刷新...")
            time.sleep(8)
            safe_rerun()
