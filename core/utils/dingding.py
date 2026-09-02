"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
import base64
import hashlib
import os.path
import requests
import json
import traceback
from datetime import datetime
from PIL import Image
from config import proxy


# 企业微信通知
def send_wechat_work_msg(content, url):
    if not url:
        print('未配置wechat_webhook_url，不发送信息')
        return
    try:
        data = {
            "msgtype": "text",
            "text": {
                "content": content + '\n' + datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        r = requests.post(url, data=json.dumps(data), timeout=10, proxies=proxy)
        print(f'调用企业微信接口返回： {r.text}')
        print('成功发送企业微信')
    except Exception as e:
        print(f"发送企业微信失败:{e}")
        print(traceback.format_exc())


# 上传图片，解析bytes
class MyEncoder(json.JSONEncoder):

    def default(self, obj):
        """
        只要检查到了是bytes类型的数据就把它转为str类型
        :param obj:
        :return:
        """
        if isinstance(obj, bytes):
            return str(obj, encoding='utf-8')
        return json.JSONEncoder.default(self, obj)


# 企业微信发送图片
def send_wechat_work_img(file_path, url):
    if not os.path.exists(file_path):
        print('找不到图片')
        return
    if not url:
        print('未配置wechat_webhook_url，不发送信息')
        return
    try:
        # =检查图片大小，如果超过 2MB 则进行压缩
        file_size = os.path.getsize(file_path)
        max_size = 2 * 1024 * 1024  # 2MB

        # 达到 98% 就压缩
        if file_size > max_size * 0.98:
            print(f'图片大小 {file_size / 1024 / 1024:.2f}MB 超过 2MB 限制，开始压缩...')
            try:
                # 打开图片
                image = Image.open(file_path)

                # 转换图片模式为 RGB（JPEG 不支持透明通道）
                if image.mode in ('RGBA', 'LA', 'P'):
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'P':
                        image = image.convert('RGBA')
                    background.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
                    image = background
                elif image.mode != 'RGB':
                    image = image.convert('RGB')

                # 定义质量级别
                quality_levels = [85, 75, 65, 55, 45]

                # 逐步尝试不同质量级别
                for quality in quality_levels:
                    print(f'尝试使用质量 {quality} 压缩图片...')
                    image.save(file_path, 'JPEG', quality=quality, optimize=True)
                    compressed_size = os.path.getsize(file_path)

                    if compressed_size <= max_size:
                        print(f'图片压缩成功，最终大小: {compressed_size / 1024 / 1024:.2f}MB，质量: {quality}')
                        break
                else:
                    # 所有质量级别都尝试后仍超过限制
                    final_size = os.path.getsize(file_path)
                    print(f'警告：图片压缩后仍为 {final_size / 1024 / 1024:.2f}MB，超过 2MB，尝试发送...')

            except Exception as e:
                print(f"图片压缩失败: {e}")
                print(traceback.format_exc())
                print("将尝试发送原图...")

        with open(file_path, 'rb') as f:
            image_content = f.read()
        image_base64 = base64.b64encode(image_content).decode('utf-8')
        md5 = hashlib.md5()
        md5.update(image_content)
        image_md5 = md5.hexdigest()
        data = {
            'msgtype': 'image',
            'image': {
                'base64': image_base64,
                'md5': image_md5
            }
        }
        # 服务器上传bytes图片的时候，json.dumps解析会出错，需要自己手动去转一下
        r = requests.post(url, data=json.dumps(data, cls=MyEncoder, indent=4), timeout=10, proxies=proxy)
        print(f'调用企业微信接口返回： {r.text}')
        print('成功发送企业微信')
    except Exception as e:
        print(f"发送企业微信失败:{e}")
        print(traceback.format_exc())
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def send_msg_for_order(order_param, order_res, url):
    """
    发送下单信息，只有出问题才会推送，正常下单不在推送信息
    """
    if not url:
        print('未配置wechat_webhook_url，不发送信息')
        return
    msg = ''
    try:
        for _ in range(len(order_param)):
            if 'msg' in order_res[_].keys():
                msg += f'币种:{order_param[_]["symbol"]}\n'
                msg += f'方向:{"做多" if order_param[_]["side"] == "BUY" else "做空"}\n'
                msg += f'价格:{order_param[_]["price"]}\n'
                msg += f'数量:{order_param[_]["quantity"]}\n'
                msg += f'下单结果:{order_res[_]["msg"]}'
                msg += '\n' * 2
    except BaseException as e:
        print('send_msg_for_order ERROR', e)
        print(traceback.format_exc())

    if msg:
        send_wechat_work_msg(msg, url)
