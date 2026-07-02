"""115 API 数据模型"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class FileEntry:
    """115 网盘中的文件/目录条目"""
    id: str            # 文件/目录 ID
    name: str          # 名称
    is_dir: bool       # 是否为目录
    cid: str = ""      # 所在目录 ID
    size: int = 0      # 文件大小（字节）
    pick_code: str = ""  # 提取码
    sha1: str = ""     # 文件哈希
    updated_at: str = ""  # 更新时间
    created_at: str = ""  # 创建时间


@dataclass
class ShareInfo:
    """115 分享信息"""
    share_code: str    # 分享码
    receive_code: str  = ""  # 提取码
    url: str = ""
    file_name: str = ""
    file_size: int = 0


@dataclass
class RecycleBinItem:
    """回收站条目"""
    rid: str            # 回收站记录 ID
    file_name: str      # 文件名
    file_size: int = 0  # 文件大小
    is_dir: bool = False  # 是否为目录
    cid: str = ""       # 原所在目录 CID
    parent_name: str = ""  # 原所在目录名称
    deleted_at: str = ""  # 删除时间
