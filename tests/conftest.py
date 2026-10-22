import os
import tempfile

# 测试使用独立的 AGENT_HOME，避免读写真实的限速锁文件 / .env / WebUI 设置；
# 必须在导入 agent_115 之前设置（路径在模块导入时确定）
os.environ.setdefault("PAN115_HOME", tempfile.mkdtemp(prefix="115-agent-test-"))
