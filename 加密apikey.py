"""
邢不行｜策略分享会
仓位管理实盘框架

版权所有 ©️ 邢不行
微信: xbx1717

本代码仅供个人学习使用，未经授权不得复制、修改或用于商业用途。

Author: 邢不行
"""
from core.utils.encryptor import AESEncryptor

"""
这个工具就是个专门给文本内容加密的“贴心小助手”。

纯粹又透明：它唯一的功能就是加密和解密，干干净净，没有藏任何“后门”或小心思，代码是公开的，你可以放心。

离线也能用：就算断网了，它照样能正常工作，你的数据根本不需要离开你的电脑，特别安心。

加密很靠谱：它用的是公认安全的 AES 加密算法。而且每次加密时，都会随机“加料”（专业点叫随机IV），所以哪怕你对同一段话用同一个密码加密，每次出来的结果也都是全新的，安全性更高啦！

密码很安全：解密用的密码只是临时放在内存中撑个场子，程序一关闭就会自动清除，像阅后即焚一样，确保不会在实盘电脑里留下任何记录。

PS：如果发现缺少三方库，可以更新 xbx-py11，或者手动安装 pip install pycryptodome
"""


# 密钥（最多32位长度）
aes_password = ""
# apikey 明文
apikey = ""
# secret 明文
secret = ""

encryptor = AESEncryptor(aes_password)

encrypted_apikey = encryptor.encrypt(apikey)
encrypted_secret = encryptor.encrypt(secret)

print('=' * 64)
print('注意事项：')
print('实盘使用加密后的 apikey 和 secret 配置账户')
print('启动实盘之前需要设置环境变量，并给它设置你的加密密钥，否则实盘无法启动：export X3S_TRADING_SECRET_KEY=密钥')
print('为保障安全性，相同密钥和明文，每次加密结果都是不一样的')
print('=' * 64)
print('加密后 apikey: ', encrypted_apikey)
print('加密后 secret: ', encrypted_secret)

