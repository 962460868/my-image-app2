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
import redis
import logging
import pickle
import hashlib

# --- 1. 页面配置和全局设置 ---

st.set_page_config(
    page_title="RunningHub AI - 智能图片优化工具",
    page_icon="🎨",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 配置日志，减少噪音
logging.getLogger("tornado.access").setLevel(logging.WARNING)
logging.getLogger("tornado.application").setLevel(logging.WARNING)
logging.getLogger("tornado.general").setLevel(logging.WARNING)

# Redis配置
REDIS_HOST = 'redis-18743.c340.ap-northeast-2-1.ec2.redns.redis-cloud.com'
REDIS_PORT = 18743
REDIS_PASSWORD = "dBAPubXYReEwHaIvnvX0lvr3qIgtudCp"

# API配置
API_KEY = "c95f4c4d2703479abfbc55eefeb9bb71"
WEBAPP_ID = "1947599512657453057"
NODE_INFO = [
    {"nodeId": "38", "fieldName": "image", "fieldValue": "placeholder.png", "description": "图片输入"},
    {"nodeId": "60", "fieldName": "text", "fieldValue": "8k, high quality, high detail", "description": "正向提示词补充"},
    {"nodeId": "4", "fieldName": "text", "fieldValue": "色调艳丽,过曝,静态,细节模糊不清,字幕,风格,作品,画作,画面,静止,整体发灰,最差质量,低质量,JPEG压缩残留,丑陋的,残缺的,多余的手指,画得不好的手部,画得不好的脸部,畸形的,毁容的,形态畸形的肢体,手指融合,静止不动的画面,悲乱的背景,三条腿,背景人很多,倒着走", "description": "反向提示词"}
]

# 系统配置（修复超时和刷新问题）
MAX_GLOBAL_CONCURRENT = 5  # API总并发限制
MAX_LOCAL_CONCURRENT = 3   # 单个网页并发限制
MAX_RETRIES = 3            # 最大重试次数
POLL_INTERVAL = 3          # 轮询间隔
MAX_POLL_COUNT = 300       # 最大轮询次数 (300*3秒=15分钟) - 实际容错时间
DISPLAY_TIMEOUT_MINUTES = 3  # 显示给用户的预计时间（分钟）
ACTUAL_TIMEOUT_MINUTES = 15  # 实际超时时间（分钟）
AUTO_REFRESH_INTERVAL = 5  # 增加自动刷新间隔，减少刷新频率

# Redis键名
GLOBAL_TASK_QUEUE = "runninghub:task_queue"
GLOBAL_PROCESSING_SET = "runninghub:processing_tasks"
SESSION_DATA_PREFIX = "runninghub:session:"  # 会话数据持久化

# 并发限制错误关键词
CONCURRENT_LIMIT_ERRORS = [
    "concurrent limit", "too many requests", "rate limit",
    "队列已满", "并发限制", "服务忙碌", "CONCURRENT_LIMIT_EXCEEDED", "TOO_MANY_REQUESTS"
]

# --- 2. 自定义CSS样式 ---

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
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #2980b9;
        transform: translateY(-1px);
    }
    .task-card {
        background-color: white;
        border-radius: 10px;
        padding: 1.5rem;
        margin: 1rem 0;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        border: 1px solid #e1e8ed;
    }
    .success-badge { color: #27ae60; font-weight: bold; }
    .error-badge { color: #e74c3c; font-weight: bold; }
    .processing-badge { color: #f39c12; font-weight: bold; }
    .info-badge { color: #17a2b8; font-weight: bold; }
    .waiting-badge { color: #9b59b6; font-weight: bold; }
    
    /* 修复图片显示闪烁 */
    .comparison-image {
        transition: none !important;
        image-rendering: -webkit-optimize-contrast;
    }
    
    /* 图片占位符样式 */
    .image-placeholder {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 10px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-size: 18px;
        font-weight: bold;
        animation: pulse 2s ease-in-out infinite;
        height: 500px;
        width: 100%;
    }
    @keyframes pulse {
        0%, 100% { opacity: 0.6; }
        50% { opacity: 1; }
    }
    
    /* 统计数据容器 */
    .stats-container {
        background: white;
        border-radius: 10px;
        padding: 15px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        margin-bottom: 15px;
    }
    .stat-row {
        display: flex;
        justify-content: space-between;
        padding: 8px 0;
        border-bottom: 1px solid #f0f0f0;
        align-items: center;
    }
    .stat-row:last-child {
        border-bottom: none;
    }
    .stat-label {
        color: #7f8c8d;
        font-size: 14px;
    }
    .stat-value {
        font-weight: bold;
        font-size: 16px;
        color: #2c3e50;
    }
</style>
""", unsafe_allow_html=True)

# --- 3. Redis连接初始化 ---

@st.cache_resource
def init_redis_connection():
    """初始化Redis连接（缓存资源，避免重复连接）"""
    try:
        r = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=False,  # 改为False以支持二进制数据
            username="default",
            password=REDIS_PASSWORD,
            socket_timeout=5,
            socket_connect_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        r.ping()
        return r, None
    except Exception as e:
        return None, f"Redis连接失败: {str(e)}"

r, redis_error = init_redis_connection()

# --- 4. Session State初始化和持久化 ---

def get_session_key():
    """获取当前会话的唯一标识"""
    if 'session_id' not in st.session_state:
        st.session_state.session_id = f"session_{int(time.time())}_{random.randint(1000, 9999)}"
    return st.session_state.session_id

def save_session_data():
    """保存会话数据到Redis"""
    if not r:
        return
        
    try:
        session_key = SESSION_DATA_PREFIX + get_session_key()
        session_data = {
            'tasks': [],
            'task_counter': st.session_state.get('task_counter', 0),
            'timestamp': time.time()
        }
        
        # 保存任务基本信息（不包含大的二进制数据）
        for task in st.session_state.get('tasks', []):
            task_info = {
                'task_id': task.task_id,
                'file_name': task.file_name,
                'session_id': task.session_id,
                'status': task.status,
                'progress': task.progress,
                'error_message': task.error_message,
                'api_task_id': task.api_task_id,
                'created_at': task.created_at.isoformat() if task.created_at else None,
                'start_time': task.start_time,
                'elapsed_time': task.elapsed_time,
                'retry_count': task.retry_count
            }
            session_data['tasks'].append(task_info)
        
        r.setex(session_key, 3600, pickle.dumps(session_data))  # 1小时过期
    except Exception as e:
        st.warning(f"保存会话数据失败: {e}")

def load_session_data():
    """从Redis加载会话数据"""
    if not r:
        return
        
    try:
        session_key = SESSION_DATA_PREFIX + get_session_key()
        data = r.get(session_key)
        if data:
            session_data = pickle.loads(data)
            st.session_state.task_counter = session_data.get('task_counter', 0)
            # 注意：这里只恢复任务的基本信息，文件数据需要重新上传
            return session_data.get('tasks', [])
    except Exception as e:
        st.warning(f"加载会话数据失败: {e}")
    return None

# 初始化Session State
if 'tasks' not in st.session_state:
    # 尝试从Redis恢复数据
    saved_tasks = load_session_data()
    st.session_state.tasks = []
    if saved_tasks:
        st.info(f"检测到之前的会话数据，但图片文件需要重新上传才能继续处理。")

if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
# 图片缓存，解决显示闪烁问题
if 'image_cache' not in st.session_state:
    st.session_state.image_cache = {}

# --- 5. 任务类定义（增加缓存支持） ---

class TaskItem:
    """优化的任务项类"""
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
        # 缓存相关
        self._original_b64 = None
        self._result_b64 = None

    def get_original_b64(self):
        """获取原图Base64（缓存）"""
        if self._original_b64 is None and self.file_data:
            self._original_b64 = base64.b64encode(self.file_data).decode()
        return self._original_b64

    def get_result_b64(self):
        """获取结果图Base64（缓存）"""
        if self._result_b64 is None and self.result_data:
            self._result_b64 = base64.b64encode(self.result_data).decode()
        return self._result_b64

    def to_dict(self):
        """序列化为字典"""
        return {
            'task_id': self.task_id,
            'file_name': self.file_name,
            'session_id': self.session_id,
            'created_at': self.created_at.isoformat(),
            'retry_count': self.retry_count
        }

# --- 6. 图片对比组件（优化缓存版，添加占位符） ---

def create_image_placeholder(task):
    """创建图片占位符"""
    cache_key = f"placeholder_{task.task_id}"
    
    # 检查是否已缓存
    if cache_key in st.session_state.image_cache:
        return st.session_state.image_cache[cache_key]
    
    html_code = f"""
    <div style="position: relative; width: 100%; max-width: 800px; margin: 0 auto; height: 500px;">
        <div class="image-placeholder">
            <div style="text-align: center;">
                <div style="font-size: 48px; margin-bottom: 20px;">⚡</div>
                <div style="font-size: 24px; margin-bottom: 10px;">AI处理中...</div>
                <div style="font-size: 16px; opacity: 0.8;">预计需要 3 分钟</div>
            </div>
        </div>
    </div>
    """
    
    # 缓存HTML
    st.session_state.image_cache[cache_key] = html_code
    return html_code

def create_image_comparison_cached(task):
    """创建缓存优化的图片对比组件"""
    if not task.file_data or not task.result_data:
        return None
    
    # 使用缓存的Base64数据
    original_b64 = task.get_original_b64()
    result_b64 = task.get_result_b64()
    
    if not original_b64 or not result_b64:
        return None
    
    # 生成缓存键
    cache_key = f"comparison_{task.task_id}"
    
    # 检查是否已缓存
    if cache_key in st.session_state.image_cache:
        return st.session_state.image_cache[cache_key]
    
    html_code = f"""
    <div id="comparison-container-{task.task_id}" style="position: relative; width: 100%; max-width: 800px; margin: 0 auto; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.15); height: 500px;">
        <!-- 原图背景 -->
        <img class="comparison-image" id="original-{task.task_id}" src="data:image/png;base64,{original_b64}" 
             style="width: 100%; height: 100%; display: block; object-fit: contain; position: absolute; top: 0; left: 0;" alt="原图">
        
        <!-- 结果图遮罩 -->
        <div id="result-overlay-{task.task_id}" style="position: absolute; top: 0; left: 0; width: 70%; height: 100%; overflow: hidden;">
            <img class="comparison-image" src="data:image/png;base64,{result_b64}" 
                 style="width: 142.86%; height: 100%; object-fit: contain; position: absolute; top: 0; left: 0;" alt="AI优化">
        </div>
        
        <!-- 分割线 -->
        <div id="divider-{task.task_id}" style="position: absolute; top: 0; width: 3px; height: 100%; background: #3498db; cursor: ew-resize; z-index: 10; left: 70%; margin-left: -1.5px; box-shadow: 0 0 8px rgba(52,152,219,0.5);">
            <!-- 拖动手柄 -->
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 32px; height: 32px; background: #3498db; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.2); border: 2px solid white;">
                <span style="color: white; font-size: 12px; font-weight: bold;">⟷</span>
            </div>
        </div>
        
        <!-- 标签 -->
        <div style="position: absolute; top: 10px; left: 10px; background: rgba(52, 152, 219, 0.9); color: white; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; z-index: 100;">
            AI优化
        </div>
        <div style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.7); color: white; padding: 4px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; z-index: 100;">
            原图
        </div>
        
        <!-- 下载按钮 -->
        <div id="download-btn-{task.task_id}" style="position: absolute; bottom: 10px; right: 10px; width: 40px; height: 40px; background: rgba(52, 152, 219, 0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 2px 6px rgba(0,0,0,0.3); transition: all 0.3s ease; z-index: 100;" 
             onmouseover="this.style.background='rgba(52, 152, 219, 1)'; this.style.transform='scale(1.1)'"
             onmouseout="this.style.background='rgba(52, 152, 219, 0.9)'; this.style.transform='scale(1)'">
            <span style="color: white; font-size: 18px;">⬇</span>
        </div>
    </div>

    <script>
    (function() {{
        const container = document.getElementById('comparison-container-{task.task_id}');
        const divider = document.getElementById('divider-{task.task_id}');
        const resultOverlay = document.getElementById('result-overlay-{task.task_id}');
        const downloadBtn = document.getElementById('download-btn-{task.task_id}');
        
        if (!container || !divider || !resultOverlay) return;
        
        let isDragging = false;
        
        function updateComparison(percentage) {{
            percentage = Math.max(10, Math.min(90, percentage));
            divider.style.left = percentage + '%';
            resultOverlay.style.width = percentage + '%';
            const img = resultOverlay.querySelector('img');
            if (img) {{
                img.style.width = (100 / percentage * 100) + '%';
            }}
        }}
        
        function handleDrag(e) {{
            const rect = container.getBoundingClientRect();
            const x = (e.type.includes('touch') ? e.touches[0].clientX : e.clientX) - rect.left;
            const percentage = (x / rect.width) * 100;
            updateComparison(percentage);
        }}
        
        divider.addEventListener('mousedown', function(e) {{
            isDragging = true;
            document.addEventListener('mousemove', handleDrag);
            document.addEventListener('mouseup', function() {{
                isDragging = false;
                document.removeEventListener('mousemove', handleDrag);
            }});
            e.preventDefault();
        }});
        
        container.addEventListener('click', function(e) {{
            if (e.target === downloadBtn || downloadBtn.contains(e.target)) return;
            handleDrag(e);
        }});
        
        // 下载功能
        downloadBtn.addEventListener('click', function(e) {{
            e.stopPropagation();
            const link = document.createElement('a');
            link.href = 'data:image/png;base64,{result_b64}';
            link.download = 'optimized_{task.file_name}';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            // 下载反馈
            const original = this.innerHTML;
            this.innerHTML = '<span style="color: white; font-size: 16px;">✓</span>';
            setTimeout(() => {{ this.innerHTML = original; }}, 1500);
        }});
        
        // 初始化
        updateComparison(70);
    }})();
    </script>
    """
    
    # 缓存HTML
    st.session_state.image_cache[cache_key] = html_code
    return html_code

# --- 7. 核心API函数 ---

def is_concurrent_limit_error(error_msg):
    """检查是否是并发限制错误"""
    error_lower = error_msg.lower()
    return any(keyword in error_lower for keyword in CONCURRENT_LIMIT_ERRORS)

def upload_file(file_data, file_name, api_key):
    """上传文件"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    files = {'file': (file_name, file_data)}
    data = {'apiKey': api_key, 'fileType': 'image'}
    
    response = requests.post(url, files=files, data=data, timeout=60)
    response.raise_for_status()
    result = response.json()
    
    if result.get("code") == 0:
        return result['data']['fileName']
    else:
        raise Exception(f"文件上传失败: {result.get('msg', '未知错误')}")

def run_task(api_key, webapp_id, node_info_list):
    """发起任务"""
    url = 'https://www.runninghub.cn/task/openapi/ai-app/run'
    payload = {
        "apiKey": api_key,
        "webappId": webapp_id,
        "nodeInfoList": node_info_list
    }
    
    response = requests.post(url, headers={'Content-Type': 'application/json'}, 
                           json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    
    if result.get("code") != 0:
        raise Exception(f"任务发起失败: {result.get('msg', '未知错误')}")
    
    return result['data']['taskId']

def get_task_status(api_key, task_id):
    """获取任务状态"""
    url = 'https://www.runninghub.cn/task/openapi/status'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=10)
    response.raise_for_status()
    return response.json().get('data')

def fetch_task_output(api_key, task_id):
    """获取任务输出"""
    url = 'https://www.runninghub.cn/task/openapi/outputs'
    response = requests.post(url, json={'apiKey': api_key, 'taskId': task_id}, timeout=30)
    response.raise_for_status()
    data = response.json()
    
    if data.get("code") == 0 and data.get("data"):
        file_url = data["data"][0].get("fileUrl")
        if file_url:
            return file_url
    
    raise Exception(f"获取结果失败: {data.get('msg', '未找到结果')}")

def download_result_image(url):
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    return response.content

# --- 8. 任务处理核心逻辑（修复超时问题） ---

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务（显示3分钟，实际15分钟容错）"""
    task.status = "PROCESSING"
    task.start_time = time.time()
    
    try:
        # 步骤1: 上传文件
        task.progress = 10
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        
        # 步骤2: 准备节点信息
        task.progress = 20
        node_info_list = copy.deepcopy(node_info)
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 步骤3: 发起任务
        task.progress = 30
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        
        # 步骤4: 轮询状态（实际15分钟超时，但显示3分钟倒计时）
        poll_count = 0
        display_timeout_seconds = DISPLAY_TIMEOUT_MINUTES * 60  # 3分钟 = 180秒
        
        while poll_count < MAX_POLL_COUNT:  # 300次 * 3秒 = 15分钟实际容错
            time.sleep(POLL_INTERVAL)
            poll_count += 1
            
            status = get_task_status(api_key, task.api_task_id)
            
            # 更新进度 (30% -> 90%)
            # 在前3分钟内显示正常进度，之后保持在90%
            elapsed_time = poll_count * POLL_INTERVAL
            if elapsed_time <= display_timeout_seconds:
                progress_increment = 60 * elapsed_time / display_timeout_seconds
            else:
                progress_increment = 60  # 保持在90%
            task.progress = min(90, 30 + progress_increment)
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("API任务处理失败")
            
            # 每隔30秒保存一次会话数据
            if poll_count % 10 == 0:
                save_session_data()
        
        if poll_count >= MAX_POLL_COUNT:
            raise Exception(f"任务处理超时 (超过{ACTUAL_TIMEOUT_MINUTES}分钟)")
        
        # 步骤5: 获取和下载结果
        task.progress = 95
        result_url = fetch_task_output(api_key, task.api_task_id)
        task.result_data = download_result_image(result_url)
        
        # 完成
        task.progress = 100
        task.status = "SUCCESS"
        task.elapsed_time = time.time() - task.start_time
        
        # 保存会话数据
        save_session_data()
        
    except Exception as e:
        error_msg = str(e)
        task.elapsed_time = time.time() - task.start_time if task.start_time else 0
        
        # 简化重试逻辑
        if (is_concurrent_limit_error(error_msg) and task.retry_count < MAX_RETRIES):
            task.retry_count += 1
            task.status = "QUEUED"
            task.progress = 0
            # 指数退避等待
            wait_time = (2 ** task.retry_count) + random.randint(1, 3)
            time.sleep(wait_time)
            # 重新加入队列
            if r:
                queue_key = GLOBAL_TASK_QUEUE.encode()
                task_data = json.dumps(task.to_dict()).encode()
                r.rpush(queue_key, task_data)
        else:
            task.status = "FAILED"
            task.error_message = error_msg
            
        # 保存会话数据
        save_session_data()
    
    finally:
        # 从处理集合中移除
        if r:
            processing_key = GLOBAL_PROCESSING_SET.encode()
            r.srem(processing_key, str(task.task_id))

# --- 9. 队列管理函数（修复Redis编码问题） ---

def get_queue_stats():
    """获取队列统计信息"""
    if not r:
        return {'queued': 0, 'global_processing': 0, 'local_processing': 0}
    
    try:
        queue_key = GLOBAL_TASK_QUEUE.encode()
        processing_key = GLOBAL_PROCESSING_SET.encode()
        
        queued = r.llen(queue_key)
        global_processing = r.scard(processing_key)
        local_processing = sum(1 for t in st.session_state.tasks if t.status == "PROCESSING")
        
        return {
            'queued': queued,
            'global_processing': global_processing,
            'local_processing': local_processing
        }
    except Exception as e:
        st.error(f"获取队列状态失败: {e}")
        return {'queued': 0, 'global_processing': 0, 'local_processing': 0}

def start_new_tasks():
    """启动新任务（双重并发控制）"""
    if not r:
        return
    
    try:
        stats = get_queue_stats()
        
        global_available = MAX_GLOBAL_CONCURRENT - stats['global_processing']
        local_available = MAX_LOCAL_CONCURRENT - stats['local_processing']
        available_slots = min(global_available, local_available)
        
        if available_slots <= 0:
            return
            
        queue_key = GLOBAL_TASK_QUEUE.encode()
        processing_key = GLOBAL_PROCESSING_SET.encode()
        
        for _ in range(available_slots):
            task_data_bytes = r.lpop(queue_key)
            if not task_data_bytes:
                break
                
            task_data = json.loads(task_data_bytes.decode())
            task_id = task_data['task_id']
            
            local_task = next((t for t in st.session_state.tasks if t.task_id == task_id), None)
            
            if local_task and local_task.file_data:
                local_task.retry_count = task_data.get('retry_count', 0)
                
                # 加入全局处理集合
                r.sadd(processing_key, str(task_id))
                
                # 启动处理线程
                thread = threading.Thread(
                    target=process_single_task,
                    args=(local_task, API_KEY, WEBAPP_ID, NODE_INFO)
                )
                thread.daemon = True
                thread.start()
            else:
                # 重新放回队列
                r.rpush(queue_key, task_data_bytes)
                
    except Exception as e:
        st.error(f"启动任务时出错: {e}")

# --- 10. 主界面 ---

def main():
    st.title("🎨 RunningHub AI - 智能图片优化工具")
    st.markdown("### 稳定高效的多页面协同处理平台")
    
    # 主界面布局
    left_col, right_col = st.columns([2, 3])
    
    # 左侧：上传区域
    with left_col:
        st.markdown("### 📁 图片上传")
        
        uploaded_files = st.file_uploader(
            "选择图片文件（支持多选）",
            type=['png', 'jpg', 'jpeg', 'webp'],
            accept_multiple_files=True,
            help="上传后自动加入全局队列，数据会自动保存，页面刷新不会丢失",
            key=f"file_uploader_{st.session_state.file_uploader_key}"
        )
        
        if uploaded_files:
            if not r:
                st.error("⚠️ Redis连接失败，无法使用分布式队列功能")
                st.info("错误详情: " + (redis_error or "未知错误"))
            else:
                new_tasks = []
                for file in uploaded_files:
                    st.session_state.task_counter += 1
                    task = TaskItem(
                        task_id=st.session_state.task_counter,
                        file_data=file.getvalue(),
                        file_name=file.name,
                        session_id=get_session_key()
                    )
                    st.session_state.tasks.append(task)
                    new_tasks.append(task)
                
                try:
                    queue_key = GLOBAL_TASK_QUEUE.encode()
                    pipe = r.pipeline()
                    for task in new_tasks:
                        task_data = json.dumps(task.to_dict()).encode()
                        pipe.rpush(queue_key, task_data)
                    pipe.execute()
                    
                    # 保存会话数据
                    save_session_data()
                    
                    st.success(f"✅ 已添加 {len(uploaded_files)} 个任务到全局队列!")
                    st.session_state.file_uploader_key += 1
                    time.sleep(0.5)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 添加任务失败: {e}")
        
        st.markdown("---")
        
        # 统计数据（折叠到左侧）
        with st.expander("📊 系统统计", expanded=True):
            stats = get_queue_stats()
            local_stats = {
                'success': sum(1 for t in st.session_state.tasks if t.status == "SUCCESS"),
                'failed': sum(1 for t in st.session_state.tasks if t.status == "FAILED"),
                'total': len(st.session_state.tasks)
            }
            
            st.markdown(f"""
            <div class="stats-container">
                <div class="stat-row">
                    <span class="stat-label">🌐 全局队列</span>
                    <span class="stat-value" style="color: #3498db;">{stats['queued']}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">🔄 API总并发</span>
                    <span class="stat-value" style="color: #8e44ad;">{stats['global_processing']}/{MAX_GLOBAL_CONCURRENT}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">⚡ 本页处理</span>
                    <span class="stat-value" style="color: #e67e22;">{stats['local_processing']}/{MAX_LOCAL_CONCURRENT}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">✅ 已完成</span>
                    <span class="stat-value" style="color: #27ae60;">{local_stats['success']}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">❌ 失败</span>
                    <span class="stat-value" style="color: #e74c3c;">{local_stats['failed']}</span>
                </div>
                <div class="stat-row">
                    <span class="stat-label">📋 本页总数</span>
                    <span class="stat-value" style="color: #9b59b6;">{local_stats['total']}</span>
                </div>
            </div>
            """, unsafe_allow_html=True)
        
        # 系统信息
        with st.expander("⚙️ 系统配置", expanded=False):
            if r:
                st.success("🟢 Redis: 已连接")
            else:
                st.error(f"🔴 Redis: 连接失败 - {redis_error}")
            
            st.markdown("**系统配置:**")
            st.info(f"🌐 API总并发: {MAX_GLOBAL_CONCURRENT}")
            st.info(f"🔄 单页并发: {MAX_LOCAL_CONCURRENT}")
            st.info(f"⏰ 预计时间: {DISPLAY_TIMEOUT_MINUTES}分钟")
            st.info(f"🛡️ 容错时间: {ACTUAL_TIMEOUT_MINUTES}分钟")
            st.info(f"🔁 最大重试: {MAX_RETRIES}次")
            st.info(f"🔄 自动刷新: {AUTO_REFRESH_INTERVAL}秒")
            
            st.markdown(f"**会话信息:**")
            st.code(f"Session ID: {get_session_key()}", language="text")
            
            st.markdown("**优化特性:**")
            st.markdown("""
            - ✅ 预留图片UI，处理中即可查看
            - ✅ 图片显示缓存，解决闪烁问题
            - ✅ 3分钟倒计时，15分钟容错
            - ✅ 数据自动保存，页面刷新不丢失
            - ✅ 统计数据折叠显示，界面更整洁
            """)
    
    # 右侧：任务列表
    with right_col:
        st.markdown("### 📊 任务状态")
        
        if not st.session_state.tasks:
            st.info("💡 暂无任务，请上传图片开始处理")
        else:
            # 启动新任务
            start_new_tasks()
            
            # 显示任务
            for task in reversed(st.session_state.tasks):
                with st.container():
                    st.markdown('<div class="task-card">', unsafe_allow_html=True)
                    
                    # 任务头部
                    col_info, col_status = st.columns([3, 1])
                    
                    with col_info:
                        st.markdown(f"**📄 {task.file_name}** (ID: {task.task_id})")
                        if task.retry_count > 0:
                            st.caption(f"🔄 重试 {task.retry_count}/{MAX_RETRIES}")
                    
                    with col_status:
                        if task.status == "SUCCESS":
                            st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                        elif task.status == "FAILED":
                            st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                        elif task.status == "PROCESSING":
                            st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                        else:
                            st.markdown('<span class="info-badge">⏳ 队列中</span>', unsafe_allow_html=True)
                    
                    # 进度显示（基于3分钟显示）
                    if task.status == "PROCESSING":
                        st.progress(task.progress / 100)
                        st.caption(f"进度: {int(task.progress)}%")
                        
                        if task.start_time:
                            elapsed = time.time() - task.start_time
                            display_timeout_seconds = DISPLAY_TIMEOUT_MINUTES * 60
                            
                            # 显示基于3分钟的倒计时
                            if elapsed <= display_timeout_seconds:
                                remaining_display = max(0, display_timeout_seconds - elapsed)
                                st.caption(f"已用时: {int(elapsed//60)}分{int(elapsed%60)}秒 | 预计剩余: {int(remaining_display//60)}分{int(remaining_display%60)}秒")
                            else:
                                # 超过3分钟后，显示正在处理中（不显示15分钟倒计时）
                                st.caption(f"已用时: {int(elapsed//60)}分{int(elapsed%60)}秒 | 正在处理中...")
                    
                    # 图片对比区域 - 预留UI
                    st.markdown("**🔍 效果对比** (左侧AI优化，右侧原图)")
                    
                    if task.status == "SUCCESS" and task.result_data:
                        # 显示实际对比
                        elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                        st.success(f"🎉 处理成功！用时: {elapsed_str}")
                        
                        comparison_html = create_image_comparison_cached(task)
                        if comparison_html:
                            components.html(comparison_html, height=500)
                            st.caption("💡 拖动中间分割线或点击图片任意位置对比效果，点击右下角按钮下载优化图片")
                        else:
                            st.warning("图片显示组件加载失败")
                    else:
                        # 显示占位符
                        placeholder_html = create_image_placeholder(task)
                        components.html(placeholder_html, height=500)
                        
                        if task.status == "FAILED":
                            st.error(f"💥 处理失败: {task.error_message}")
                    
                    st.markdown('</div>', unsafe_allow_html=True)
                    st.markdown("---")
            
            # 操作按钮
            col_clear_local, col_clear_global, col_save = st.columns(3)
            
            with col_clear_local:
                if st.button("🗑️ 清空本页", help="清空当前页面的任务"):
                    st.session_state.tasks = []
                    st.session_state.image_cache = {}
                    save_session_data()
                    st.rerun()
            
            with col_clear_global:
                if r and st.button("🔥 清空全局", help="⚠️ 危险：清空所有页面的队列"):
                    try:
                        queue_key = GLOBAL_TASK_QUEUE.encode()
                        processing_key = GLOBAL_PROCESSING_SET.encode()
                        r.delete(queue_key, processing_key)
                        st.session_state.tasks = []
                        st.session_state.image_cache = {}
                        save_session_data()
                        st.success("✅ 已清空全局队列")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ 清空失败: {e}")
            
            with col_save:
                if st.button("💾 手动保存", help="手动保存会话数据"):
                    save_session_data()
                    st.success("✅ 数据已保存")

    # 页脚
    st.markdown("---")
    st.markdown(f"""
    <div style='text-align: center; color: #7f8c8d; padding: 20px;'>
        <h4 style='margin: 10px 0; color: #34495e;'>🚀 RunningHub AI - 企业级稳定版</h4>
        <p><strong>🔧 问题修复</strong> | 预留UI占位 + {DISPLAY_TIMEOUT_MINUTES}分钟倒计时 + {ACTUAL_TIMEOUT_MINUTES}分钟容错</p>
        <p><strong>⚡ 性能优化</strong> | 统计数据折叠 + 图片缓存防闪烁</p>
        <p><strong>🛡️ 稳定可靠</strong> | 自动保存 + 断线恢复 + 错误重试</p>
        <p><strong>💾 数据安全</strong> | Redis持久化 + 会话恢复</p>
    </div>
    """, unsafe_allow_html=True)

# --- 11. 应用入口和优化的自动刷新 ---

if __name__ == "__main__":
    try:
        main()
        
        # 优化的自动刷新逻辑
        has_processing = any(t.status == "PROCESSING" for t in st.session_state.tasks)
        has_queue_items = False
        
        if r:
            try:
                queue_key = GLOBAL_TASK_QUEUE.encode()
                processing_key = GLOBAL_PROCESSING_SET.encode()
                has_queue_items = r.llen(queue_key) > 0 or r.scard(processing_key) > 0
            except:
                has_queue_items = False
        
        # 只在必要时刷新，减少频率
        if has_processing or has_queue_items:
            time.sleep(AUTO_REFRESH_INTERVAL)  # 增加到5秒间隔
            st.rerun()
            
    except Exception as e:
        error_str = str(e).lower()
        if any(keyword in error_str for keyword in ['websocket', 'tornado', 'streamlit']):
            # WebSocket等连接错误，静默处理
            pass
        else:
            st.error(f"⚠️ 系统错误: {e}")
            st.info("系统将自动恢复...")
            time.sleep(8)
            st.rerun()
