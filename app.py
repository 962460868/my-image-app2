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
import os
from urllib.parse import urlparse

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
    .token-waiting-badge {
        color: #ff6b6b;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

# Redis配置 - 从环境变量读取
def get_redis_config():
    """从环境变量获取Redis配置"""
    redis_url = os.getenv('REDIS_URL')
    if redis_url:
        # 解析Redis URL
        parsed = urlparse(redis_url)
        return {
            'host': parsed.hostname,
            'port': parsed.port or 6379,
            'password': parsed.password,
            'db': 0
        }
    else:
        # 从单独的环境变量读取
        return {
            'host': os.getenv('REDIS_HOST', 'localhost'),
            'port': int(os.getenv('REDIS_PORT', 6379)),
            'password': os.getenv('REDIS_PASSWORD'),
            'db': int(os.getenv('REDIS_DB', 0))
        }

REDIS_CONFIG = get_redis_config()
TOKEN_BUCKET_KEY = "ai_processing_tokens"  # Redis中令牌桶的键名
GLOBAL_CONCURRENT_LIMIT = 5  # 全局并发限制

# 初始化 session_state
if 'tasks' not in st.session_state:
    st.session_state.tasks = []
if 'task_counter' not in st.session_state:
    st.session_state.task_counter = 0
if 'file_uploader_key' not in st.session_state:
    st.session_state.file_uploader_key = 0
if 'redis_client' not in st.session_state:
    st.session_state.redis_client = None

# 配置常量
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

class RedisTokenManager:
    """Redis令牌桶管理器"""
    
    def __init__(self, redis_client, bucket_key, max_tokens):
        self.redis_client = redis_client
        self.bucket_key = bucket_key
        self.max_tokens = max_tokens
        self.init_tokens()
    
    def init_tokens(self):
        """初始化令牌桶"""
        try:
            # 检查令牌桶是否已存在
            current_tokens = self.redis_client.llen(self.bucket_key)
            
            if current_tokens == 0:
                # 如果令牌桶为空，初始化令牌
                for i in range(self.max_tokens):
                    token_id = f"token_{i+1}_{int(time.time())}"
                    self.redis_client.lpush(self.bucket_key, token_id)
                st.success(f"✅ 已初始化 {self.max_tokens} 个全局处理令牌")
            else:
                st.info(f"ℹ️ 发现现有令牌桶，当前可用令牌数：{current_tokens}")
        except Exception as e:
            st.error(f"❌ 初始化令牌桶失败：{str(e)}")
    
    def acquire_token(self, timeout=0):
        """获取令牌（阻塞操作）"""
        try:
            if timeout > 0:
                result = self.redis_client.brpop(self.bucket_key, timeout=timeout)
            else:
                result = self.redis_client.brpop(self.bucket_key, timeout=0)
            
            if result:
                return result[1].decode('utf-8')  # 返回令牌ID
            return None
        except Exception as e:
            logging.error(f"获取令牌失败：{str(e)}")
            return None
    
    def release_token(self, token_id):
        """释放令牌"""
        try:
            self.redis_client.lpush(self.bucket_key, token_id)
            return True
        except Exception as e:
            logging.error(f"释放令牌失败：{str(e)}")
            return False
    
    def get_available_tokens(self):
        """获取当前可用令牌数量"""
        try:
            return self.redis_client.llen(self.bucket_key)
        except:
            return 0
    
    def get_processing_count(self):
        """获取当前处理中的任务数量"""
        return max(0, self.max_tokens - self.get_available_tokens())

@st.cache_resource
def get_redis_client():
    """获取Redis连接（使用Streamlit缓存）"""
    try:
        client = redis.Redis(
            host=REDIS_CONFIG['host'],
            port=REDIS_CONFIG['port'],
            password=REDIS_CONFIG['password'],
            db=REDIS_CONFIG['db'],
            decode_responses=False,  # 手动处理编码
            socket_timeout=10,
            socket_connect_timeout=10,
            retry_on_timeout=True,
            health_check_interval=30
        )
        # 测试连接
        client.ping()
        return client
    except Exception as e:
        st.error(f"❌ Redis连接失败：{str(e)}")
        st.error(f"配置信息: {REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}")
        return None

class TaskItem:
    """任务项类"""
    def __init__(self, task_id, file_data, file_name):
        self.task_id = task_id
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
        self.retry_count = 0  # 重试次数
        self.max_retries = 10  # 最大重试次数
        self.token_id = None  # 当前持有的令牌ID
        self.waiting_for_token = False  # 是否正在等待令牌

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
        
        <!-- 标签 -->
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
            percentage = Math.max(5, Math.min(95, percentage));
            divider.style.left = percentage + '%';
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
        
        if (downloadBtn) {{
            downloadBtn.addEventListener('click', function(e) {{
                e.stopPropagation();
                const link = document.createElement('a');
                link.href = 'data:image/png;base64,{result_b64}';
                link.download = 'optimized_image.png';
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);
                
                const originalText = downloadBtn.innerHTML;
                downloadBtn.innerHTML = '<div style="color: white; font-size: 12px; font-weight: bold;">✓</div>';
                setTimeout(() => {{
                    downloadBtn.innerHTML = originalText;
                }}, 1000);
            }});
        }}
        
        updateComparison(70);
        
        divider.addEventListener('mousedown', startDrag);
        divider.addEventListener('touchstart', startDrag);
        
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
        uploaded_filename = response_data['data']['fileName']
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
    
    task_id = run_data['data']['taskId']
    return task_id

def fetch_task_output(api_key, task_id):
    """获取任务输出"""
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
    """下载结果图片"""
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    content = response.content
    return content

def process_single_task(task, api_key, webapp_id, node_info, token_manager):
    """处理单个任务（含令牌管理）"""
    token_id = None
    try:
        # 第一步：获取令牌（阻塞操作）
        task.status = "WAITING_TOKEN"
        task.waiting_for_token = True
        
        # 阻塞等待令牌，最多等待300秒（5分钟）
        token_id = token_manager.acquire_token(timeout=300)
        
        if not token_id:
            raise Exception("获取处理令牌超时，请稍后重试")
        
        task.token_id = token_id
        task.waiting_for_token = False
        task.status = "UPLOADING"
        task.start_time = time.time()
        task.progress = 5
        
        # 步骤2: 上传文件
        uploaded_filename = upload_file(task.file_data, task.file_name, api_key)
        task.progress = 15
        
        # 步骤3: 准备节点信息
        node_info_list = copy.deepcopy(node_info)
        
        # 更新图片节点
        for node in node_info_list:
            if node["nodeId"] == "38":
                node["fieldValue"] = uploaded_filename
        
        # 步骤4: 发起任务
        task.api_task_id = run_task(api_key, webapp_id, node_info_list)
        task.status = "PROCESSING"
        task.progress = 20
        
        # 步骤5: 轮询状态
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
            
            # 缓慢增长进度条：从20%到95%
            if progress < 95:
                progress += min(2, (95 - progress) / 10)  # 越接近95%增长越慢
                progress = int(progress)
            
            task.progress = progress
            
            if status == "SUCCESS":
                break
            elif status == "FAILED":
                raise Exception("任务处理失败")
            elif status in ["QUEUED", "RUNNING"]:
                # 继续等待
                continue
            else:
                continue
        
        # 检查是否超时
        if poll_count >= max_polls:
            raise Exception("任务处理超时")
        
        # 只有在状态为SUCCESS时才获取结果
        if status == "SUCCESS":
            # 步骤6: 获取结果
            task.progress = 95
            result_url = fetch_task_output(api_key, task.api_task_id)
            task.result_url = result_url
            
            # 步骤7: 下载结果
            task.result_data = download_result_image(result_url)
            task.progress = 100
            task.status = "SUCCESS"
            task.elapsed_time = time.time() - task.start_time
        else:
            raise Exception(f"任务未成功完成，最终状态: {status}")
            
    except Exception as e:
        error_msg = str(e)
        
        # 检查是否是并发限制错误
        if is_concurrent_limit_error(error_msg) and task.retry_count < task.max_retries:
            # 并发限制错误，回到队列等待重试
            task.status = "WAITING"  # 新状态：等待重试
            task.retry_count += 1
            task.progress = 0
            # 随机等待2-10秒后重试，避免所有任务同时重试
            wait_time = random.randint(2, 10)
            time.sleep(wait_time)
            task.status = "QUEUED"  # 重新排队
        else:
            # 其他错误或超过最大重试次数
            task.status = "FAILED"
            task.error_message = error_msg
            task.elapsed_time = time.time() - task.start_time if task.start_time else 0
    finally:
        # 确保释放令牌
        if token_id:
            token_manager.release_token(token_id)
            task.token_id = None
        task.waiting_for_token = False

# 初始化Redis连接和令牌管理器
redis_client = get_redis_client()

if redis_client:
    if 'token_manager' not in st.session_state:
        st.session_state.token_manager = RedisTokenManager(
            redis_client, 
            TOKEN_BUCKET_KEY, 
            GLOBAL_CONCURRENT_LIMIT
        )

# 主界面
st.title("🎨 RunningHub AI - 智能图片优化工具（Redis分布式版）")
st.markdown("### 专业的AI图片优化和增强服务 - 支持多实例水平扩容")

# Redis连接状态
if redis_client:
    st.success("✅ Redis连接正常")
    
    # 获取令牌状态
    if 'token_manager' in st.session_state:
        available_tokens = st.session_state.token_manager.get_available_tokens()
        processing_count = st.session_state.token_manager.get_processing_count()
    else:
        available_tokens = 0
        processing_count = 0
else:
    st.error("❌ Redis连接失败，无法使用分布式并发控制")
    st.error("请检查环境变量配置")
    st.stop()

# 统计信息
col1, col2, col3, col4, col5, col6 = st.columns(6)
with col1:
    queued = sum(1 for t in st.session_state.tasks if t.status == "QUEUED")
    st.metric("队列中", queued)
with col2:
    waiting_token = sum(1 for t in st.session_state.tasks if t.status == "WAITING_TOKEN")
    st.metric("等待令牌", waiting_token)
with col3:
    processing_local = sum(1 for t in st.session_state.tasks if t.status in ["UPLOADING", "PROCESSING"])
    st.metric(f"本地处理中", processing_local)
with col4:
    st.metric(f"全局处理中", f"{processing_count}/{GLOBAL_CONCURRENT_LIMIT}")
with col5:
    completed = sum(1 for t in st.session_state.tasks if t.status == "SUCCESS")
    st.metric("已完成", completed)
with col6:
    failed = sum(1 for t in st.session_state.tasks if t.status == "FAILED")
    st.metric("失败", failed)

# 令牌状态显示
col_token1, col_token2 = st.columns(2)
with col_token1:
    st.info(f"🎫 可用令牌数：{available_tokens}")
with col_token2:
    st.info(f"🔄 全局处理中：{processing_count}")

st.markdown("---")

# 左右分栏
left_col, right_col = st.columns([2, 3])

with left_col:
    st.markdown("### 📁 图片上传")
    
    uploaded_files = st.file_uploader(
        "选择图片文件（支持多选）",
        type=['png', 'jpg', 'jpeg', 'webp'],
        accept_multiple_files=True,
        help="可以一次选择多张图片进行批量处理，上传后自动加入处理队列",
        key=f"file_uploader_{st.session_state.file_uploader_key}"
    )
    
    # 自动加入队列逻辑
    if uploaded_files:
        # 添加文件到任务队列
        for uploaded_file in uploaded_files:
            st.session_state.task_counter += 1
            task = TaskItem(
                task_id=st.session_state.task_counter,
                file_data=uploaded_file.getvalue(),
                file_name=uploaded_file.name
            )
            st.session_state.tasks.append(task)
        
        st.success(f"已添加 {len(uploaded_files)} 个任务到队列！")
        
        # 清空文件上传框
        st.session_state.file_uploader_key += 1
        st.rerun()
    
    st.markdown("---")
    
    # 队列状态说明
    with st.expander("📊 状态说明", expanded=False):
        st.markdown("""
        - **队列中**: 等待开始处理
        - **等待令牌**: 正在等待获取全局处理令牌
        - **本地处理中**: 本实例正在处理的任务
        - **全局处理中**: 所有实例正在处理的任务总数
        - **已完成**: 处理成功
        - **失败**: 处理失败（超过重试次数）
        
        **Redis分布式特性**：
        - 全局并发限制：{} 个任务
        - 多实例共享令牌桶
        - 自动负载均衡
        """.format(GLOBAL_CONCURRENT_LIMIT))
    
    with st.expander("⚙️ Redis & API 配置", expanded=False):
        st.markdown("**Redis配置：**")
        st.text(f"Host: {REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}")
        st.text(f"Database: {REDIS_CONFIG['db']}")
        st.text(f"令牌桶Key: {TOKEN_BUCKET_KEY}")
        st.text(f"全局并发限制: {GLOBAL_CONCURRENT_LIMIT}")
        
        st.markdown("**API配置：**")
        st.text_input("API Key", value=API_KEY, disabled=True)
        st.text_input("WebApp ID", value=WEBAPP_ID, disabled=True)

with right_col:
    st.markdown("### 📊 任务队列")
    
    if not st.session_state.tasks:
        st.info("暂无任务，请上传图片开始处理")
    else:
        # 启动新任务的逻辑 - 移除了本地并发限制，完全依赖Redis令牌
        for task in st.session_state.tasks:
            if task.status == "QUEUED" and 'token_manager' in st.session_state:
                thread = threading.Thread(
                    target=process_single_task,
                    args=(task, API_KEY, WEBAPP_ID, NODE_INFO, st.session_state.token_manager)
                )
                thread.daemon = True
                thread.start()
        
        # 显示所有任务
        for task in reversed(st.session_state.tasks):
            with st.container():
                st.markdown(f'<div class="task-card">', unsafe_allow_html=True)
                
                col_title, col_status = st.columns([3, 1])
                with col_title:
                    st.markdown(f"**📄 {task.file_name}** (Task-{task.task_id})")
                    if task.retry_count > 0:
                        st.caption(f"重试次数: {task.retry_count}/{task.max_retries}")
                    if task.token_id:
                        st.caption(f"🎫 持有令牌: {task.token_id}")
                with col_status:
                    if task.status == "SUCCESS":
                        st.markdown('<span class="success-badge">✅ 完成</span>', unsafe_allow_html=True)
                    elif task.status == "FAILED":
                        st.markdown('<span class="error-badge">❌ 失败</span>', unsafe_allow_html=True)
                    elif task.status in ["UPLOADING", "PROCESSING"]:
                        st.markdown('<span class="processing-badge">⚡ 处理中</span>', unsafe_allow_html=True)
                    elif task.status == "WAITING_TOKEN":
                        st.markdown('<span class="token-waiting-badge">🎫 等待令牌</span>', unsafe_allow_html=True)
                    elif task.status == "WAITING":
                        st.markdown('<span class="waiting-badge">⏳ 等待重试</span>', unsafe_allow_html=True)
                    else:
                        st.markdown('<span class="info-badge">⏸️ 队列中</span>', unsafe_allow_html=True)
                
                # 进度条
                if task.status in ["UPLOADING", "PROCESSING"]:
                    st.progress(task.progress / 100)
                    st.caption(f"进度: {task.progress}%")
                    
                    if task.start_time:
                        elapsed = time.time() - task.start_time
                        remaining = max(0, 180 - elapsed)  # 预计3分钟
                        minutes = int(remaining // 60)
                        seconds = int(remaining % 60)
                        st.caption(f"剩余时间: 约{minutes}分{seconds}秒")
                elif task.status == "WAITING_TOKEN":
                    st.warning("🎫 正在等待获取全局处理令牌...")
                elif task.status == "WAITING":
                    st.info("API服务繁忙，正在等待重试...")
                
                # 结果显示 - 使用滑动对比
                if task.status == "SUCCESS" and task.result_data:
                    elapsed_str = f"{int(task.elapsed_time//60)}分{int(task.elapsed_time%60)}秒"
                    st.success(f"✅ 处理完成！用时: {elapsed_str}")
                    
                    # 显示滑动对比组件
                    st.markdown("**🔍 原图 vs AI优化对比**（拖动中间线或点击任意位置对比，点击右下角图标下载）")
                    comparison_html = create_before_after_comparison(task.file_data, task.result_data, task.task_id)
                    components.html(comparison_html, height=600)
                    
                    # 使用说明
                    st.caption("💡 左侧显示AI优化效果，右侧显示原图。拖动中间线或点击图片任意位置进行对比。")
                
                elif task.status == "FAILED":
                    st.error(f"❌ 处理失败: {task.error_message}")
                    if task.retry_count >= task.max_retries:
                        st.warning("已达到最大重试次数")
                
                st.markdown('</div>', unsafe_allow_html=True)
                st.markdown("---")
        
        if st.button("🗑️ 清空所有任务"):
            st.session_state.tasks = []
            st.rerun()

# 页脚
st.markdown("---")
st.markdown(f"""
<div style='text-align: center; color: #7f8c8d;'>
    <p>🚀 支持Redis分布式令牌桶，全局并发限制={GLOBAL_CONCURRENT_LIMIT}个任务</p>
    <p>📤 多实例水平扩容，自动负载均衡和令牌管理</p>
    <p>🔍 完成后支持原图与AI优化图片的滑动对比预览，点击图片右下角图标直接下载</p>
    <p>🎫 当前可用令牌: {available_tokens}/{GLOBAL_CONCURRENT_LIMIT}</p>
</div>
""", unsafe_allow_html=True)

# 自动刷新
if any(t.status in ["UPLOADING", "PROCESSING", "WAITING", "WAITING_TOKEN"] for t in st.session_state.tasks):
    time.sleep(2)
    st.rerun()
