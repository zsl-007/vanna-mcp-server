"""
配置管理模块 — 支持环境变量和 YAML 两种配置方式。

优先级：YAML 配置文件 > 环境变量 > 默认值。
敏感信息（密码、API Key）建议通过环境变量设置，YAML 中可用 ${VAR} 语法引用。

使用示例:
    # 方式 1：纯环境变量
    config = load_config()

    # 方式 2：YAML 配置文件（环境变量作为回退）
    config = load_config(yaml_path="config.yaml")

    # 方式 3：代码中直接构造
    config = AppConfig(
        database=DatabaseConfig(type="highgo", host="127.0.0.1", port=5866, ...),
        ...
    )
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


# ---------------------------------------------------------------------------
# 数据结构定义
# ---------------------------------------------------------------------------


@dataclass
class DatabaseConfig:
    """数据库连接配置。

    Attributes:
        type: 数据库类型，可选值 mysql / postgresql / highgo / dm / sqlite / sqlserver。
        host: 数据库主机地址（SQLite 不需要）。
        port: 数据库端口（SQLite 无端口概念，设为 0）。
        name: 数据库名称（DM 中为 schema 名，SQLite 中为文件路径）。
        user: 数据库用户名（SQLite 不需要）。
        password: 数据库密码（SQLite 不需要）。
    """

    type: str = "postgresql"
    host: str = "127.0.0.1"
    port: int = 5432
    name: str = "postgres"
    user: str = "postgres"
    password: str = ""

    def __post_init__(self) -> None:
        """根据数据库类型设置默认端口和用户名（未显式配置时）。"""
        # SQLite 特殊处理：无端口和用户概念
        if self.type.lower() == "sqlite":
            self.port = 0
            self.user = ""  # SQLite 无用户概念，强制清空
            return  # SQLite 不需要默认端口/用户填充

        defaults: dict[str, tuple[int, str]] = {
            "mysql": (3306, "root"),
            "postgresql": (5432, "postgres"),
            "highgo": (5866, "sysdba"),
            "dm": (5236, "SYSDBA"),
            "sqlserver": (1433, "sa"),
        }
        default_port, default_user = defaults.get(self.type.lower(), (5432, "postgres"))
        if self.port == 0 or self.port == 5432:
            # 仅当端口仍为 dataclass 默认值且类型不同时才覆盖
            if self.type.lower() != "postgresql":
                self.port = default_port
        if not self.user:
            self.user = default_user

    def to_dict(self) -> dict[str, Any]:
        """转换为适配器工厂所需的配置字典。"""
        return {
            "host": self.host,
            "port": self.port,
            "database": self.name,
            "user": self.user,
            "password": self.password,
        }


@dataclass
class LLMConfig:
    """LLM 大语言模型配置。

    Attributes:
        model: 模型名称（如 gpt-4, gpt-3.5-turbo）。
        api_key: OpenAI API Key（或其他兼容 LLM 的 API Key）。
        base_url: OpenAI 兼容 API 的 Base URL，空字符串表示使用 OpenAI 官方地址。
        temperature: 生成温度，控制随机性，默认 0.7。
    """

    model: str = "gpt-4"
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7


@dataclass
class VectorStoreConfig:
    """向量存储配置。

    Attributes:
        type: 向量存储类型，当前固定为 chromadb。
        path: ChromaDB 持久化路径，训练数据按数据库类型隔离。
    """

    type: str = "chromadb"
    path: str = "./chromadb_data"


@dataclass
class MCPConfig:
    """MCP Server 配置。

    Attributes:
        host: 监听地址。
        port: 监听端口。
        transport: 传输方式，默认 streamable-http。
        stateless: 是否使用无状态模式（生产推荐 True）。
        json_response: 是否使用 JSON 响应格式（生产推荐 True）。
        max_request_body_size: 请求体最大字节数，默认 8MB。
    """

    host: str = "0.0.0.0"
    port: int = 8000
    transport: str = "streamable-http"
    stateless: bool = True
    json_response: bool = True
    max_request_body_size: int = 8 * 1024 * 1024  # 8 MB


@dataclass
class AppConfig:
    """应用全局配置。

    Attributes:
        database: 数据库连接配置。
        llm: LLM 模型配置。
        vector_store: 向量存储配置。
        mcp: MCP Server 配置。
    """

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)


# ---------------------------------------------------------------------------
# YAML 环境变量引用解析
# ---------------------------------------------------------------------------

_ENV_VAR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_vars(value: Any) -> Any:
    """递归解析 YAML 值中的 ${VAR} 环境变量引用。

    Args:
        value: YAML 解析后的任意值（str / dict / list 等）。

    Returns:
        解析环境变量引用后的值。未找到的环境变量返回空字符串。
    """
    if isinstance(value, str):
        return _ENV_VAR_PATTERN.sub(
            lambda m: os.getenv(m.group(1), ""), value
        )
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def _get_env(key: str, default: str = "") -> str:
    """读取环境变量，返回去除首尾空白后的值。"""
    val = os.getenv(key, default)
    return val.strip() if isinstance(val, str) else val


def _get_env_int(key: str, default: int = 0) -> int:
    """读取环境变量并转为 int，转换失败时返回 default。"""
    raw = _get_env(key, str(default))
    try:
        return int(raw)
    except (ValueError, TypeError):
        return default


def _get_env_bool(key: str, default: bool = False) -> bool:
    """读取环境变量并转为 bool。"""
    raw = _get_env(key, str(default)).lower()
    return raw in ("true", "1", "yes", "on")


def _load_yaml(yaml_path: str) -> dict[str, Any]:
    """加载 YAML 配置文件并解析其中的环境变量引用。

    Args:
        yaml_path: YAML 文件路径。

    Returns:
        解析后的配置字典。文件不存在时返回空字典。
    """
    path = Path(yaml_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return _resolve_env_vars(raw)


def _load_database_config(yaml_data: dict[str, Any]) -> DatabaseConfig:
    """从 YAML 和环境变量合并加载数据库配置。

    优先级：YAML > 环境变量 > dataclass 默认值。
    """
    db_yaml = yaml_data.get("database", {})

    return DatabaseConfig(
        type=db_yaml.get("type") or _get_env("DB_TYPE", "postgresql"),
        host=db_yaml.get("host") or _get_env("DB_HOST", "127.0.0.1"),
        port=db_yaml.get("port") or _get_env_int("DB_PORT", 0),
        name=db_yaml.get("name") or _get_env("DB_NAME", "postgres"),
        user=db_yaml.get("user") or _get_env("DB_USER", ""),
        password=db_yaml.get("password") or _get_env("DB_PASSWORD", ""),
    )


def _load_llm_config(yaml_data: dict[str, Any]) -> LLMConfig:
    """从 YAML 和环境变量合并加载 LLM 配置。"""
    llm_yaml = yaml_data.get("llm", {})

    return LLMConfig(
        model=llm_yaml.get("model") or _get_env("LLM_MODEL", "gpt-4"),
        api_key=llm_yaml.get("api_key") or _get_env("OPENAI_API_KEY", ""),
        base_url=llm_yaml.get("base_url") or _get_env("LLM_BASE_URL", ""),
        temperature=llm_yaml.get("temperature", 0.7),
    )


def _load_vector_store_config(
    yaml_data: dict[str, Any], db_type: str
) -> VectorStoreConfig:
    """从 YAML 和环境变量合并加载向量存储配置。

    训练数据按数据库类型隔离：若未显式指定 CHROMADB_PATH，
    则自动生成 ``./chromadb_data_{db_type}`` 路径。
    """
    vs_yaml = yaml_data.get("vector_store", {})
    env_path = _get_env("CHROMADB_PATH", "")
    yaml_path = vs_yaml.get("path", "")

    if yaml_path:
        path = yaml_path
    elif env_path:
        path = env_path
    else:
        # 按数据库类型隔离训练数据
        path = f"./chromadb_data_{db_type.lower()}"

    return VectorStoreConfig(
        type=vs_yaml.get("type", "chromadb"),
        path=path,
    )


def _load_mcp_config(yaml_data: dict[str, Any]) -> MCPConfig:
    """从 YAML 和环境变量合并加载 MCP Server 配置。"""
    mcp_yaml = yaml_data.get("mcp", {})

    return MCPConfig(
        host=mcp_yaml.get("host") or _get_env("MCP_HOST", "0.0.0.0"),
        port=mcp_yaml.get("port") or _get_env_int("MCP_PORT", 8000),
        transport=mcp_yaml.get("transport", "streamable-http"),
        stateless=mcp_yaml.get("stateless", True),
        json_response=mcp_yaml.get("json_response", True),
        max_request_body_size=mcp_yaml.get(
            "max_request_body_size", 8 * 1024 * 1024
        ),
    )


def load_config(yaml_path: Optional[str] = None) -> AppConfig:
    """加载应用配置。

    配置优先级：YAML 配置文件 > 环境变量 > 默认值。
    当 ``yaml_path`` 为 None 时，仅从环境变量加载。

    Args:
        yaml_path: YAML 配置文件路径。为 None 时仅使用环境变量。

    Returns:
        AppConfig 实例。

    Raises:
        FileNotFoundError: 当 yaml_path 指定的文件不存在时（仅在非 None 时）。
    """
    yaml_data: dict[str, Any] = {}
    if yaml_path:
        yaml_data = _load_yaml(yaml_path)
        if not yaml_data:
            raise FileNotFoundError(
                f"YAML 配置文件不存在或为空: {yaml_path}"
            )

    database = _load_database_config(yaml_data)
    llm = _load_llm_config(yaml_data)
    vector_store = _load_vector_store_config(yaml_data, database.type)
    mcp = _load_mcp_config(yaml_data)

    return AppConfig(
        database=database,
        llm=llm,
        vector_store=vector_store,
        mcp=mcp,
    )


def get_config() -> AppConfig:
    """获取全局配置单例（懒加载）。

    首次调用时从环境变量 CONFIG_YAML_PATH 指定的 YAML 文件加载
    （未设置则仅使用环境变量），后续调用返回缓存实例。

    Returns:
        AppConfig 实例。
    """
    global _config_instance
    if _config_instance is None:
        yaml_path = _get_env("CONFIG_YAML_PATH", "")
        _config_instance = load_config(
            yaml_path=yaml_path if yaml_path else None
        )
    return _config_instance


# 全局配置单例
_config_instance: Optional[AppConfig] = None


# ---------------------------------------------------------------------------
# YAML 配置文件示例生成
# ---------------------------------------------------------------------------

EXAMPLE_YAML = """\
# ============================================================
# Vanna MCP Server 配置文件示例
# 使用方式: CONFIG_YAML_PATH=config.yaml python server.py
# 或在代码中: config = load_config(yaml_path="config.yaml")
# ============================================================

# --- 数据库配置 ---
database:
  type: highgo              # mysql | postgresql | highgo | dm | sqlite | sqlserver
  host: 192.168.1.100
  port: 5866                # MySQL:3306  PG:5432  HighGo:5866  DM:5236  SQLServer:1433  SQLite:无
  name: highgo              # 数据库名（DM 中为 schema 名，SQLite 中为文件路径如 /path/to/data.db）
  user: sysdba              # MySQL:root  PG:postgres  HighGo:sysdba  DM:SYSDBA  SQLServer:sa  SQLite:无
  password: ${DB_PASSWORD}  # 支持环境变量引用，避免明文存储（SQLite 不需要）

# --- LLM 大语言模型配置 ---
llm:
  model: gpt-4
  api_key: ${OPENAI_API_KEY}
  base_url: ${LLM_BASE_URL}    # OpenAI 兼容 API base URL，留空使用 OpenAI 官方
  temperature: 0.7

# --- 向量存储配置 ---
# 训练数据按数据库类型自动隔离，路径默认为 ./chromadb_data_{db_type}
vector_store:
  type: chromadb
  path: ./chromadb_data_highgo

# --- MCP Server 配置 ---
mcp:
  transport: streamable-http
  host: 0.0.0.0
  port: 8000
  stateless: true           # 无状态模式，生产推荐
  json_response: true       # JSON 响应格式，生产推荐
  max_request_body_size: 8388608  # 8MB，支持大 DDL 训练数据
"""

EXAMPLE_ENV = """\
# ============================================================
# Vanna MCP Server 环境变量配置示例
# 使用方式: source .env 或通过 docker-compose / systemd 注入
# ============================================================

# --- 数据库配置 ---
DB_TYPE=highgo               # mysql | postgresql | highgo | dm | sqlite | sqlserver
DB_HOST=192.168.1.100
DB_PORT=5866                 # MySQL:3306  PG:5432  HighGo:5866  DM:5236  SQLServer:1433  SQLite:无
DB_NAME=highgo               # SQLite 中为文件路径如 /path/to/data.db（:memory: 为内存数据库）
DB_USER=sysdba               # MySQL:root  PG:postgres  HighGo:sysdba  DM:SYSDBA  SQLServer:sa  SQLite:无
DB_PASSWORD=your_password    # SQLite 不需要密码

# --- LLM 配置 ---
LLM_MODEL=gpt-4
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx
LLM_BASE_URL=                   # 留空使用 OpenAI 官方，或填入兼容 API 地址

# --- 向量存储配置 ---
# 不设置时自动按数据库类型隔离: ./chromadb_data_{db_type}
# CHROMADB_PATH=./chromadb_data_highgo

# --- MCP Server 配置 ---
MCP_HOST=0.0.0.0
MCP_PORT=8000
"""


def generate_example_configs(output_dir: str = ".") -> None:
    """生成 YAML 和 .env 示例配置文件到指定目录。

    Args:
        output_dir: 输出目录路径。
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    (out / "config.yaml.example").write_text(EXAMPLE_YAML, encoding="utf-8")
    (out / ".env.example").write_text(EXAMPLE_ENV, encoding="utf-8")
