import streamlit as st
import requests
import time
from PIL import Image
import io
from datetime import datetime

# 页面配置
st.set_page_config(
    page_title="RunningHub AI 图片优化",
    page_icon="🎨",
    layout="wide"
)

# 自定义CSS样式
st.markdown("""
<style>
    .stButton>button {
        width: 100%;
        background-color: #3498db;
        color: white;
        height: 3em;
        border-radius: 8px;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #2980b9;
    }
    .success-box {
        padding: 1rem;
        border-radius: 8px;
        background-color: #d4edda;
        border: 1px solid #c3e6cb;
        margin: 1rem 0;
    }
    .info-box {
        padding: 1rem;
        border-radius: 8px;
        background-color: #d1ecf1;
        border: 1px solid #bee5eb;
        margin: 1rem 0;
    }
</style>
""", unsafe_allow_html=True)

# API配置（预填好的）
API_KEY = "c95f4c4d2703479abfbc55eefeb9bb71"
WEBAPP_ID = "1947599512657453057"
NODE_INFO = [
    {"nodeId": "38", "fieldName": "image", "fieldValue": "placeholder.png", "description": "图片输入"},
    {"nodeId": "60", "fieldName": "text", "fieldValue": "8k, high quality, high detail", "description": "正向提示词补充"},
    {"nodeId": "4", "fieldName": "text", "fieldValue": "色调艳丽,过曝,静态,细节模糊不清,字幕,风格,作品,画作,画面,静止,整体发灰,最差质量,低质量,JPEG压缩残留,丑陋的,残缺的,多余的手指,画得不好的手部,画得不好的脸部,畸形的,毁容的,形态畸形的肢体,手指融合,静止不动的画面,悲乱的背景,三条腿,背景人很多,倒着走", "description": "反向提示词"}
]

def upload_image(image_file):
    """上传图片到服务器"""
    url = 'https://www.runninghub.cn/task/openapi/upload'
    
    files = {'file': (image_file.name, image_file.getvalue())}
    data = {'apiKey': API_KEY, 'fileType': 'image'}
    
    try:
        response = requests.post(url, files=files, data=data, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        if result.get("code") == 0:
            return result['data']['fileName']
        else:
            st.error(f"上传失败: {result.get('msg', '未知错误')}")
            return None
    except Exception as e:
        st.error(f"上传出错: {str(e)}")
        return None

def start_task(uploaded_filename):
    """发起处理任务"""
    url = 'https://www.runninghub.cn/task/openapi/run'
    headers = {'Content-Type': 'application/json'}
    
    # 更新图片文件名
    node_info_list = NODE_INFO.copy()
    node_info_list[0]["fieldValue"] = uploaded_filename
    
    payload = {
        "apiKey": API_KEY,
        "webappId": WEBAPP_ID,
        "nodeInfoList": node_info_list
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if result.get("code") == 0:
            return result['data']['taskId']
        else:
            st.error(f"任务启动失败: {result.get('msg', '未知错误')}")
            return None
    except Exception as e:
        st.error(f"启动任务出错: {str(e)}")
        return None

def check_task_status(task_id):
    """检查任务状态"""
    url = 'https://www.runninghub.cn/task/openapi/status'
    
    try:
        response = requests.post(url, json={'apiKey': API_KEY, 'taskId': task_id}, timeout=10)
        response.raise_for_status()
        result = response.json()
        return result.get('data')
    except Exception as e:
        st.error(f"查询状态出错: {str(e)}")
        return None

def get_task_result(task_id):
    """获取任务结果"""
    url = 'https://www.runninghub.cn/task/openapi/outputs'
    
    try:
        response = requests.post(url, json={'apiKey': API_KEY, 'taskId': task_id}, timeout=30)
        response.raise_for_status()
        result = response.json()
        
        if result.get("code") == 0 and result.get("data"):
            file_url = result["data"][0].get("fileUrl")
            return file_url
        else:
            st.error("获取结果失败")
            return None
    except Exception as e:
        st.error(f"获取结果出错: {str(e)}")
        return None

def download_image(image_url):
    """下载处理后的图片"""
    try:
        response = requests.get(image_url, timeout=60)
        response.raise_for_status()
        return response.content
    except Exception as e:
        st.error(f"下载图片出错: {str(e)}")
        return None

# 主界面
st.title("🎨 RunningHub AI 智能图片优化工具")
st.markdown("上传图片，AI自动优化处理，提升画质和细节")

# 创建两列布局
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("📤 上传原图")
    uploaded_file = st.file_uploader(
        "选择图片文件",
        type=['png', 'jpg', 'jpeg', 'webp'],
        help="支持 PNG、JPG、JPEG、WEBP 格式"
    )
    
    if uploaded_file:
        # 显示原图
        st.image(uploaded_file, caption="原始图片", use_container_width=True)
        
        # 处理按钮
        if st.button("🚀 开始AI处理", type="primary"):
            with st.spinner("正在处理，请稍候..."):
                # 步骤1: 上传图片
                progress_bar = st.progress(0)
                status_text = st.empty()
                
                status_text.text("📤 正在上传图片...")
                progress_bar.progress(10)
                
                uploaded_filename = upload_image(uploaded_file)
                
                if uploaded_filename:
                    progress_bar.progress(20)
                    
                    # 步骤2: 启动任务
                    status_text.text("⚡ 启动AI处理...")
                    task_id = start_task(uploaded_filename)
                    
                    if task_id:
                        progress_bar.progress(30)
                        
                        # 步骤3: 等待处理完成
                        status_text.text("🤖 AI处理中，预计2-3分钟...")
                        
                        max_wait = 180  # 最多等3分钟
                        start_time = time.time()
                        check_interval = 3  # 每3秒检查一次
                        
                        while time.time() - start_time < max_wait:
                            status = check_task_status(task_id)
                            
                            elapsed = int(time.time() - start_time)
                            remaining = max(0, max_wait - elapsed)
                            
                            if status == "SUCCESS":
                                progress_bar.progress(90)
                                status_text.text("✅ 处理完成，正在获取结果...")
                                
                                # 获取结果
                                result_url = get_task_result(task_id)
                                if result_url:
                                    progress_bar.progress(95)
                                    status_text.text("📥 正在下载优化后的图片...")
                                    
                                    image_data = download_image(result_url)
                                    if image_data:
                                        progress_bar.progress(100)
                                        status_text.empty()
                                        progress_bar.empty()
                                        
                                        # 保存到session state
                                        st.session_state.result_image = image_data
                                        st.session_state.result_url = result_url
                                        st.rerun()
                                break
                            
                            elif status == "FAILED":
                                st.error("❌ 处理失败，请重试")
                                break
                            
                            elif status in ["QUEUED", "RUNNING"]:
                                # 更新进度（30-85%之间）
                                progress = 30 + int((elapsed / max_wait) * 55)
                                progress_bar.progress(min(progress, 85))
                                status_text.text(f"⚡ AI处理中... 剩余约{remaining//60}分{remaining%60}秒")
                            
                            time.sleep(check_interval)
                        
                        if time.time() - start_time >= max_wait:
                            st.warning("⏱️ 处理超时，请稍后刷新页面查看结果")

with col2:
    st.subheader("✨ 优化结果")
    
    # 显示处理结果
    if 'result_image' in st.session_state:
        result_img = Image.open(io.BytesIO(st.session_state.result_image))
        st.image(result_img, caption="AI优化后的图片", use_container_width=True)
        
        # 下载按钮
        st.download_button(
            label="💾 下载优化后的图片",
            data=st.session_state.result_image,
            file_name=f"optimized_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
            mime="image/png",
            type="primary"
        )
        
        # 清除按钮
        if st.button("🔄 处理新图片"):
            if 'result_image' in st.session_state:
                del st.session_state.result_image
            if 'result_url' in st.session_state:
                del st.session_state.result_url
            st.rerun()
    else:
        st.info("👈 请先上传图片并点击处理按钮")

# 页脚
st.markdown("---")
st.markdown("""
<div style='text-align: center; color: #666; font-size: 0.9em;'>
    <p>💡 提示：处理时间约2-3分钟，请耐心等待</p>
    <p>🔒 您的图片会被安全处理，完成后自动删除</p>
</div>
""", unsafe_allow_html=True)
