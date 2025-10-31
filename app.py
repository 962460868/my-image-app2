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

# 系统配置
MAX_GLOBAL_CONCURRENT = 5  # API总并发限制
MAX_LOCAL_CONCURRENT = 3   # 单个网页并发限制
MAX_RETRIES = 3            # 最大重试次数
POLL_INTERVAL = 3          # 轮询间隔
MAX_POLL_COUNT = 300       # 最大轮询次数 (300*3秒=15分钟)
AUTO_REFRESH_INTERVAL = 5  # 自动刷新间隔

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
        border-radius: 8px;
        height: 3em;
        font-weight: bold;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        transform: translateY(-1px);
    }
    /* 下载按钮样式 */
    .download-button>div>div>button {
        background-color: #27ae60 !important;
        color: white !important;
    }
    .download-button>div>div>button:hover {
        background-color: #229954 !important;
    }
    /* 对比按钮样式 */
    .compare-button>div>div>button {
        background-color: #3498db !important;
        color: white !important;
    }
    .compare-button>div>div>button:hover {
        background-color: #2980b9 !important;
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
    .metric-container {
        background: white;
        padding: 1rem;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
        text-align: center;
        border: 1px solid #e1e8ed;
    }
    .comparison-container {
        margin-top: 15px;
        padding: 15px;
        border: 2px dashed #3498db;
        border-radius: 10px;
        background-color: #f8f9fa;
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
        
        r.setex(session_key.encode(), 3600, pickle.dumps(session_data))  # 1小时过期
    except Exception as e:
        st.warning(f"保存会话数据失败: {e}")

def load_session_data():
    """从Redis加载会话数据"""
    if not r:
        return
        
    try:
        session_key = SESSION_DATA_PREFIX + get_session_key()
        data = r.get(session_key.encode())
        if data:
            session_data = pickle.loads(data)
            st.session_state.task_counter = session_data.get('task_counter', 0)
            return session_data.get('tasks', [])
    except Exception as e:
        st.warning(f"加载会话数据失败: {e}")
    return None

# 初始化Session State
if 'tasks' not in st.session_state:
    saved_tasks = load_session_data()
    st.session_state.tasks = []
    if saved_tasks:
        st.info(f"检测到之前的会话数据，图片文件需要重新上传才能继续处理。")

if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
# 对比组件显示状态
if 'comparison_states' not in st.session_state:
    st.session_state.comparison_states = {}
# 对比组件HTML缓存
if 'comparison_cache' not in st.session_state:
    st.session_state.comparison_cache = {}

# --- 5. 任务类定义 ---

class TaskItem:
    """任务项类"""
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

    def to_dict(self):
        """序列化为字典"""
        return {
            'task_id': self.task_id,
            'file_name': self.file_name,
            'session_id': self.session_id,
            'created_at': self.created_at.isoformat(),
            'retry_count': self.retry_count
        }

# --- 6. 下载功能 ---

def create_download_button(task):
    """创建下载按钮"""
    if task.result_data:
        # 生成优化后的文件名
        name_parts = task.file_name.rsplit('.', 1)
        if len(name_parts) == 2:
            download_name = f"{name_parts[0]}_optimized.{name_parts[1]}"
        else:
            download_name = f"{task.file_name}_optimized.png"
        
        return st.download_button(
            label="📥 下载优化图",
            data=task.result_data,
            file_name=download_name,
            mime="image/png",
            key=f"download_{task.task_id}",
            help="下载AI优化后的高清图片"
        )
    return False

# --- 7. 图片对比组件（优化版） ---

def create_comparison_component(task):
    """创建对比组件（只在点击时生成一次）"""
    cache_key = f"comparison_{task.task_id}"
    
    # 检查缓存
    if cache_key in st.session_state.comparison_cache:
        return st.session_state.comparison_cache[cache_key]
    
    if not task.file_data or not task.result_data:
        return None
    
    # 生成Base64
    original_b64 = base64.b64encode(task.file_data).decode()
    result_b64 = base64.b64encode(task.result_data).decode()
    
    html_code = f"""
    <div id="comparison-container-{task.task_id}" style="position: relative; width: 100%; max-width: 800px; margin: 0 auto; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.15); background: white;">
        <!-- 原图背景 -->
        <img id="original-{task.task_id}" src="data:image/png;base64,{original_b64}" 
             style="width: 100%; height: auto; display: block;" alt="原图">
        
        <!-- 结果图遮罩 -->
        <div id="result-overlay-{task.task_id}" style="position: absolute; top: 0; left: 0; width: 70%; height: 100%; overflow: hidden;">
            <img src="data:image/png;base64,{result_b64}" 
                 style="width: 142.86%; height: 100%; object-fit: cover;" alt="AI优化">
        </div>
        
        <!-- 分割线 -->
        <div id="divider-{task.task_id}" style="position: absolute; top: 0; width: 3px; height: 100%; background: #3498db; cursor: ew-resize; z-index: 10; left: 70%; margin-left: -1.5px; box-shadow: 0 0 8px rgba(52,152,219,0.5);">
            <!-- 拖动手柄 -->
            <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); width: 32px; height: 32px; background: #3498db; border-radius: 50%; display: flex; align-items: center; justify-content: center; box-shadow: 0 2px 6px rgba(0,0,0,0.2); border: 2px solid white;">
                <span style="color: white; font-size: 12px; font-weight: bold;">⟷</span>
            </div>
        </div>
        
        <!-- 标签 -->
        <div style="position: absolute; top: 10px; left: 10px; background: rgba(52, 152, 219, 0.9); color: white; padding: 6px 12px; border-radius: 15px; font-size: 12px; font-weight: bold; z-index: 100;">
            AI优化后
        </div>
        <div style="position: absolute; top: 10px; right: 10px; background: rgba(0,0,0,0.7); color: white; padding: 6px 12px; border-radius: 15px; font-size: 12px; font-weight: bold; z-index: 100;">
            原图
        </div>
        
        <!-- 内置下载按钮 -->
        <div id="download-btn-{task.task_id}" style="position: absolute; bottom: 15px; right: 15px; width: 45px; height: 45px; background: rgba(39, 174, 96, 0.9); border-radius: 50%; display: flex; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 3px 8px rgba(0,0,0,0.3); transition: all 0.3s ease; z-index: 100;" 
             onmouseover="this.style.background='rgba(39, 174, 96, 1)'; this.style.transform='scale(1.1)'"
             onmouseout="this.style.background='rgba(39, 174, 96, 0.9)'; this.style.transform='scale(1)'">
            <span style="color: white; font-size: 20px;">⬇</span>
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
            link.download = '{task.file_name.rsplit(".", 1)[0] if "." in task.file_name else task.file_name}_optimized.png';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            
            // 下载成功反馈
            const original = this.innerHTML;
            this.innerHTML = '<span style="color: white; font-size: 18px;">✓</span>';
            setTimeout(() => {{ this.innerHTML = original; }}, 2000);
        }});
        
        // 初始化
        updateComparison(70);
    }})();
    </script>
    """
    
    # 缓存HTML
    st.session_state.comparison_cache[cache_key] = html_code
    return html_code

# --- 8. 核心API函数 ---

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

# --- 9. 任务处理核心逻辑 ---

def process_single_task(task, api_key, webapp_id, node_info):
    """处理单个任务"""
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
        
        # 步骤4: 轮询状态
        poll_count = 0
        
        while poll_count < MAX_POLL_COUNT:
            time.sleep(POLL_INTERVAL)
            poll_count += 1
            
            status = get_task_status(api_key, task.api_task_id)
            
            # 更新进度
            progress_increment = 60 * poll_count / MAX_POLL_COUNT
            task.progress = min(90, 30 + progress_increment)
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("API任务处理失败")
            
            # 每隔30秒保存一次会话数据
            if poll_count % 10 == 0:
                save_session_data()
        
        if poll_count >= MAX_POLL_COUNT:
            raise Exception(f"任务处理超时 (超过{MAX_POLL_COUNT * POLL_INTERVAL // 60}分钟)")
        
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
        
        # 重试逻辑
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

# --- 10. 队列管理函数 ---

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

# --- 11. 主界面 ---

def main():
    st.title("🎨 RunningHub AI - 智能图片优化工具")
    st.markdown("### 高效稳定的按需对比显示版本")
    
    # 状态展示
    col1, col2, col3, col4, col5, col6 = st.columns(6)
    
    stats = get_queue_stats()
    local_stats = {
        'success': sum(1 for t in st.session_state.tasks if t.status == "SUCCESS"),
        'failed': sum(1 for t in st.session_state.tasks if t.status == "FAILED"),
        'total': len(st.session_state.tasks)
    }
    
    with col1:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#3498db;">{stats['queued']}</h3>
            <p style="margin:0; color:#7f8c8d;">全局队列</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#8e44ad;">{stats['global_processing']}/{MAX_GLOBAL_CONCURRENT}</h3>
            <p style="margin:0; color:#7f8c8d;">API总并发</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#e67e22;">{stats['local_processing']}/{MAX_LOCAL_CONCURRENT}</h3>
            <p style="margin:0; color:#7f8c8d;">本页处理</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col4:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#27ae60;">{local_stats['success']}</h3>
            <p style="margin:0; color:#7f8c8d;">已完成</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col5:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#e74c3c;">{local_stats['failed']}</h3>
            <p style="margin:0; color:#7f8c8d;">失败</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col6:
        st.markdown(f"""
        <div class="metric-container">
            <h3 style="margin:0; color:#9b59b6;">{local_stats['total']}</h3>
            <p style="margin:0; color:#7f8c8d;">本页总数</p>
        </div>
        """, unsafe_allow_html=True)
    
    # 优化说明
    timeout_minutes = MAX_POLL_COUNT * POLL_INTERVAL // 60
    st.success(f"✨ **按需加载**: 任务完成后点击按钮查看对比，避免自动加载卡顿 | ⏰ **超时**: {timeout_minutes}分钟")
    
    st.markdown("---")
    
    # 主界面布局
    left_col, right_col = st.columns([2, 3])
    
    # 左侧：上传区域
    with left_col:
        st.markdown("### 📁 图片上传")
        
        uploaded_files = st.file_uploader(
            "选择图片文件（支持多选）",
            type=['png', 'jpg', 'jpeg', 'webp'],
            accept_multiple_files=True,
            help="上传后任务加入全局队列，完成后按需查看对比效果",
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
                    
                    st.success(f"✅ 已添加 {len(uploaded_files)} 个任务到全局队列！")
                    st.session_state.file_uploader_key += 1
                    time.sleep(0.5)
                    st.rerun()
                    
                except Exception as e:
                    st.error(f"❌ 添加任务失败: {e}")
        
        st.markdown("---")
        
        # 系统信息
        with st.expander("⚙️ 系统配置", expanded=False):
            if r:
                st.success("🟢 Redis: 已连接")
            else:
                st.error(f"🔴 Redis: 连接失败 - {redis_error}")
            
            st.markdown("**系统配置:**")
            st.info(f"🌐 API总并发: {MAX_GLOBAL_CONCURRENT}")
            st.info(f"📄 单页并发: {MAX_LOCAL_CONCURRENT}")
            st.info(f"⏰ 单任务超时: {timeout_minutes}分钟")
            st.info(f"🔁 最大重试: {MAX_RETRIES}次")
            st.info(f"🔄 自动刷新: {AUTO_REFRESH_INTERVAL}秒")
            
            st.markdown(f"**会话信息:**")
            st.code(f"Session ID: {get_session_key()}", language="text")
            
            st.markdown("**按需加载特性:**")
            st.markdown("""
            - ✅ 任务完成后显示操作按钮
            - ✅ 点击"效果对比"才加载组件
            - ✅ 对比组件只渲染一次，不重复加载
            - ✅ 成功任务不参与自动刷新
            - ✅ 大幅提升页面响应速度
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
                    
                    # 进度显示
                    if task.status == "PROCESSING":
                        st.progress(task.progress / 100)
                        st.caption(f"进度: {int(task.progress)}%")
                        
                        if task.start_time:
                            elapsed = time.time() - task.start_time
                            remaining_estimate = max(0, (timeout_minutes * 60) - elapsed)
                            st.caption(f"已用时: {int(elapsed//60)}分{int(elapsed%60)}秒 | 剩余: 约{int(remaining_estimate//60)}分钟")
                    
                    # 成功任务的按需显示逻辑
                    elif task.status == "SUCCESS" and task.result_data:
                        elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                        st.success(f"🎉 处理成功！用时: {elapsed_str}")
                        
                        # 默认显示两个按钮
                        button_col1, button_col2 = st.columns(2)
                        
                        with button_col1:
                            # 下载按钮 (使用自定义样式)
                            st.markdown('<div class="download-button">', unsafe_allow_html=True)
                            download_clicked = create_download_button(task)
                            st.markdown('</div>', unsafe_allow_html=True)
                            
                            if download_clicked:
                                st.success(f"✅ {task.file_name} 下载开始！")
                        
                        with button_col2:
                            # 效果对比按钮
                            st.markdown('<div class="compare-button">', unsafe_allow_html=True)
                            compare_clicked = st.button(
                                "🔍 效果对比", 
                                key=f"compare_{task.task_id}",
                                help="点击查看原图与AI优化后的滑动对比效果"
                            )
                            st.markdown('</div>', unsafe_allow_html=True)
                            
                            # 点击对比按钮后，设置显示状态
                            if compare_clicked:
                                st.session_state.comparison_states[task.task_id] = True
                                st.rerun()  # 重新渲染以显示对比组件
                        
                        # 如果已点击对比按钮，显示对比组件
                        if st.session_state.comparison_states.get(task.task_id, False):
                            st.markdown('<div class="comparison-container">', unsafe_allow_html=True)
                            st.markdown("**🔍 滑动对比效果** (拖动中间线或点击任意位置对比)")
                            
                            comparison_html = create_comparison_component(task)
                            if comparison_html:
                                components.html(comparison_html, height=500)
                                st.caption("💡 左侧显示AI优化效果，右侧显示原图。可拖动分割线或点击图片进行对比。右下角绿色按钮可直接下载。")
                            else:
                                st.error("对比组件生成失败，请重试")
                            
                            st.markdown('</div>', unsafe_allow_html=True)
                    
                    # 失败任务
                    elif task.status == "FAILED":
                        st.error(f"💥 处理失败: {task.error_message}")
                    
                    st.markdown('</div>', unsafe_allow_html=True)
                    st.markdown("---")
            
            # 操作按钮
            col_clear_local, col_clear_global, col_save = st.columns(3)
            
            with col_clear_local:
                if st.button("🗑️ 清空本页", help="清空当前页面的任务"):
                    st.session_state.tasks = []
                    st.session_state.comparison_states = {}
                    st.session_state.comparison_cache = {}
                    save_session_data()
                    st.rerun()
            
            with col_clear_global:
                if r and st.button("🔥 清空全局", help="⚠️ 危险：清空所有页面的队列"):
                    try:
                        queue_key = GLOBAL_TASK_QUEUE.encode()
                        processing_key = GLOBAL_PROCESSING_SET.encode()
                        r.delete(queue_key, processing_key)
                        st.session_state.tasks = []
                        st.session_state.comparison_states = {}
                        st.session_state.comparison_cache = {}
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
        <h4 style='margin: 10px 0; color: #34495e;'>🚀 RunningHub AI - 按需加载优化版</h4>
        <p><strong>⚡ 性能优化</strong> | 按需加载对比组件，避免自动渲染卡顿</p>
        <p><strong>🎯 用户体验</strong> | 两个操作按钮：直接下载 + 效果对比</p>
        <p><strong>🔧 稳定改进</strong> | 成功任务不参与刷新，组件只渲染一次</p>
        <p><strong>💾 数据安全</strong> | Redis持久化 + 断线恢复 + {timeout_minutes}分钟超时</p>
    </div>
    """, unsafe_allow_html=True)

# --- 12. 应用入口和优化的自动刷新 ---

if __name__ == "__main__":
    try:
        main()
        
        # 优化的自动刷新逻辑：只有PROCESSING状态的任务参与刷新判断
        has_processing = any(t.status == "PROCESSING" for t in st.session_state.tasks)
        has_queue_items = False
        
        if r:
            try:
                queue_key = GLOBAL_TASK_QUEUE.encode()
                processing_key = GLOBAL_PROCESSING_SET.encode()
                has_queue_items = r.llen(queue_key) > 0 or r.scard(processing_key) > 0
            except:
                has_queue_items = False
        
        # SUCCESS任务不参与刷新判断，大幅减少刷新频率
        if has_processing or has_queue_items:
            time.sleep(AUTO_REFRESH_INTERVAL)
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
