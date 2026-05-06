"""
图片素材获取与处理工具
"""
import os
import requests
import uuid
import io
import json
import base64
import mimetypes
import traceback
from PIL import Image
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Union

from .log_utils import log_wrapper
from .config import config
from .utils import retry, timer

@retry(2)
def download_image(img_url, save_dir='images', save_path=""):
    """
    从指定图片URL下载图片并保存到本地，返回存储路径。

    参数：
    -------
    img_url: str
        图片URL链接
    save_dir: str, optional
        本地保存文件夹，默认为 "images"
    save_dir: str
        返回一个图片文件到目标路径。
    返回：
    -------
    str
        下载后图片在本地的存储路径
    """
    if save_path:
        file_path = save_path
    else:
        # 若存储目录不存在，则创建
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        # 随机生成文件名，可以根据需求改成其他命名方式
        filename = str(uuid.uuid4()) + '.jpg'
        file_path = os.path.join(save_dir, filename)
    
    # 下载图片
    log_wrapper.info(f"下载图片：{img_url}")
    
    # Check if we need to use proxy for unsplash
    unsplash_proxy = config.get("unsplash_proxy", None)
    if "unsplash" in img_url and unsplash_proxy:
        log_wrapper.info(f"使用代理{unsplash_proxy}")
        try:
            response = requests.get(img_url, 
                                    stream=True, 
                                    proxies={
                                        "http": unsplash_proxy, 
                                        "https": unsplash_proxy}
                                    )
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                log_wrapper.info(f"img_url:{img_url}, 404 Not Found - 忽略此错误")
                return file_path
            log_wrapper.info(f"img_url:{img_url}, 使用代理{unsplash_proxy}失败")
        except Exception as e:
            log_wrapper.info(f"img_url:{img_url}, 使用代理{unsplash_proxy}失败")
    else:
        response = requests.get(img_url, stream=True)
    if response.status_code == 200:
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
    return file_path


def image_to_base64(image_path):
    """
    将图像转换为 Base64 字符串，动态识别图像格式。

    :param image_path: 图像文件路径
    :return: Base64 编码的字符串
    """
    # 打开图像
    with Image.open(image_path) as img:
        # 获取图像格式
        image_format = img.format  # 动态获取格式（如 JPEG、PNG 等）
        # 创建内存字节流
        buffered = io.BytesIO()
        # 将图像保存到字节流中，使用原始格式
        img.save(buffered, format=image_format)
        # 获取字节流内容并进行 Base64 编码
        img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return img_base64

def image_url_to_base64(url):
    """
    image url 2 base64 
    """
    try:
        filepath = download_image(url, "./template_image")
        base64str = image_to_base64(filepath)
        if os.path.exists(filepath):
            os.remove(filepath)
        return base64str
    except Exception as e:
        log_wrapper.info(f"image_url_to_base64 error {traceback.format_exc()}")
        return ""

@retry(max_retries=3)
def get_image(prompt, topk=3, type="实景图"):
    """
    根据提示词获取对应匹配图片
    Args:
        prompt提示词
        topk返回结果数量
        type图片类型

    return img_url
    """
    # Get URL from config
    image_search_url = config.get("api", {}).get("image_search_url", "")
    if not image_search_url:
        log_wrapper.error("No image_search_url found in config['api']")
        return None
        
    route = "/api/v1/image_search"
    url = image_search_url
    body = {
        "text": prompt,
        "topk": topk,
        "type": type
    }
    resp = requests.post(f"{url}{route}", json=body)
    resp_json = resp.json()
    if resp_json["code"] != 0:
        raise Exception("image search result not 0")
    result = resp_json.get("data", {}).get("result", [])
    if result:
        return result[0]["imageUrl"]

def is_image_file(file_path):
    """
    判断文件是否是图片类型。
    
    参数:
        file_path (str): 文件路径。
    
    返回:
        bool: 如果是图片类型返回 True，否则返回 False。
    """
    if not os.path.isfile(file_path):
        return False
    
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type and mime_type.startswith('image'):
        return True
    
    return False

def is_image_open(file_path):
    """
    判断图片是否能打开
    """
    if not os.path.isfile(file_path):
        return False
    try:
        img = Image.open(file_path)
        return True
    except Exception as e:
        log_wrapper.info(f"{file_path}图片无法打开，需要重下")
    return False

def load_img2json(filepath):
    """
    读取图片内容
    """
    try:
        with open(filepath, "r") as fp:
            content = fp.read()
    except UnicodeDecodeError:
        log_wrapper.info(f"{filepath} 已经是图片跳过生成：\n{traceback.format_exc()}")
        return {}
    if "{" in content:
        try:
            data = json.loads(content)
            return data
        except Exception as e:
            log_wrapper.info(f"{filepath} 已经是图片跳过生成：\n{traceback.format_exc()}")
            return {}
    elif "http" in content:
        return content
    else:
        return {}

def process_single_image(img_info: Union[Dict, str], filepath: str):
    """
    Process a single image - helper function for parallel processing
    """
    try:
        log_wrapper.info(f"图片素材内容:{json.dumps(img_info, ensure_ascii=False)}")
        if isinstance(img_info, dict):
            prompt = img_info.get("description", "")
            img_url = img_info.get("url", "")
            if not prompt:
                return
            log_wrapper.info(f"图像内容：{prompt}")
            # 检索图片
            # img_url = get_image(prompt)
        else:
            log_wrapper.info(f"图片素材内容疑似错误")
            prompt = "森林"
            img_url = img_info
        
        try:
            # Remove .url extension if present before downloading
            target_path = filepath[:-4] if filepath.endswith('.url') else filepath
            if img_url:
                file_flag = download_image(img_url, save_path=target_path)
            else:
                log_wrapper.info("图片url内容为空")
                file_flag = ""
            open_flag = is_image_open(target_path) #判断文件是否可以打开
            if not file_flag or not open_flag:
                # 图片直接下载失败，走检索
                img_url = get_image(prompt)
                download_image(img_url, save_path=target_path)
            # Save image info to .url file
            with open(f"{target_path}.url", "w") as fp:
                json.dump(img_info, fp, ensure_ascii=False)
        except Exception as e:
            log_wrapper.info(f"下载图片：{img_url}有问题，跳过")
    except Exception as e:
        log_wrapper.info(f"处理图片时发生错误：{str(e)}")

@timer  
def supply_project_images(project_path):
    """
    为前端项目补充图片素材内容。使用多线程并行下载图片。
    """
    image_contents = []
    for root, dirs, files in os.walk(project_path):
        for f in files:
            filepath = os.path.join(root, f)
            if "node_modules/" in filepath or \
                "dist" in filepath:
                continue
            if not is_image_file(filepath):
                # Skip non-image files
                continue
            
            # Handle potential image content files
            img_info = load_img2json(filepath)
            if img_info:
                image_contents.append((img_info, filepath))

    if not image_contents:
        return
    # 使用线程池并行处理图片
    max_workers = min(8, len(image_contents))  # 限制最大线程数
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务到线程池
        futures = [
            executor.submit(process_single_image, img_info, filepath)
            for img_info, filepath in image_contents
        ]
        
        # 等待所有任务完成
        for future in futures:
            try:
                future.result()  # 获取结果，如果有异常会在这里抛出
            except Exception as e:
                log_wrapper.info(f"线程执行出错：{str(e)}")


