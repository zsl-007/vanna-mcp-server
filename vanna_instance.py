"""
Vanna 实例初始化模块 — 多数据库后端动态注入。

继承 Vanna Legacy 架构的 ChromaDB_VectorStore + OpenAI_Chat，
通过 DatabaseAdapterFactory 动态注入 ``run_sql`` 函数到 Vanna 实例。

训练数据按数据库类型隔离：ChromaDB 路径为 ``./chromadb_data_{db_type}``，
确保不同数据库的 DDL、文档、SQL 示例互不干扰。

接口契约:
    from vanna_instance import create_vanna
    vn = create_vanna()  # 返回配置好的 VannaBase 实例，已注入 run_sql

使用示例:
    vn = create_vanna()
    # 训练
    vn.train(ddl="CREATE TABLE users (id INT, name VARCHAR(100))")
    vn.train(question="查询所有用户", sql="SELECT * FROM users")
    # 问数
    sql = vn.generate_sql(question="有多少活跃用户？")
    df = vn.run_sql(sql)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from config import AppConfig, get_config
from database_adapter import DatabaseAdapter, DatabaseAdapterFactory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vanna 实例类 — Legacy 架构混合封装
# ---------------------------------------------------------------------------

try:
    from vanna.legacy.chromadb import ChromaDB_VectorStore
    from vanna.legacy.openai import OpenAI_Chat
    from vanna.legacy.base import VannaBase
except ImportError:
    # Vanna 未安装时提供占位类，便于类型检查和文档生成
    logger.warning(
        "Vanna 库未安装。请运行: pip install 'vanna[chromadb,openai]'"
    )
    VannaBase = object  # type: ignore[assignment, misc]

    class ChromaDB_VectorStore(VannaBase):  # type: ignore[misc]
        """ChromaDB 向量存储占位类（Vanna 未安装时使用）。"""

        def __init__(self, config=None):  # noqa: D401
            pass

    class OpenAI_Chat(VannaBase):  # type: ignore[misc]
        """OpenAI Chat LLM 占位类（Vanna 未安装时使用）。"""

        def __init__(self, config=None):  # noqa: D401
            pass


class MultiDbVanna(ChromaDB_VectorStore, OpenAI_Chat):
    """支持多数据库后端的 Vanna 实例。

    继承 Legacy 架构的 ChromaDB_VectorStore（向量存储 / RAG 检索）
    和 OpenAI_Chat（LLM 对话），通过多继承 Mixin 模式组合完整功能。

    ``run_sql`` 函数由 ``DatabaseAdapterFactory`` 动态注入，
    无需在此类中实现具体的数据库连接逻辑。

    Attributes:
        dialect: SQL 方言标识，由注入的适配器决定（如 "PostgreSQL", "DM"）。
        run_sql: SQL 执行函数，由适配器注入。
        run_sql_is_set: 标记 run_sql 是否已注入。
    """

    def __init__(self, config: Optional[dict] = None) -> None:
        """初始化 Vanna 实例。

        分别调用两个父类的初始化方法，确保 ChromaDB 向量存储
        和 OpenAI LLM 客户端都正确初始化。

        当 ``config["base_url"]`` 非空时，预构建带自定义 base_url 的
        ``OpenAI`` 客户端实例，通过 ``client`` 参数传给
        ``OpenAI_Chat.__init__()``，绕过 Vanna 库对 ``api_base`` 的限制，
        从而支持任意 OpenAI 兼容的大模型服务（如 DeepSeek、智谱、通义千问等）。
        ``base_url`` 为空时保持原有行为，直接连接 OpenAI 官方 API。

        Args:
            config: 配置字典，包含以下可选键:
                - ``model``: LLM 模型名称
                - ``api_key``: OpenAI API Key
                - ``base_url``: OpenAI 兼容 API 的 base URL（可选）
                - ``path``: ChromaDB 持久化路径
                - ``dialect``: SQL 方言
                - ``temperature``: LLM 生成温度
        """
        config = config or {}
        ChromaDB_VectorStore.__init__(self, config=config)

        # 如果配置了 base_url，预构建带自定义 base_url 的 OpenAI 客户端，
        # 通过 client 参数传入，绕过 OpenAI_Chat 对 api_base 的限制
        base_url = config.get("base_url", "")
        if base_url:
            from openai import OpenAI

            openai_client = OpenAI(
                api_key=config.get("api_key", ""),
                base_url=base_url,
            )
            OpenAI_Chat.__init__(self, client=openai_client, config=config)
        else:
            # 无 base_url 时保持原有行为
            OpenAI_Chat.__init__(self, config=config)


# ---------------------------------------------------------------------------
# Vanna 实例创建
# ---------------------------------------------------------------------------


def create_vanna(config: Optional[AppConfig] = None) -> VannaBase:
    """创建并返回配置好的 Vanna 实例。

    执行流程:
        1. 加载配置（环境变量 / YAML）
        2. 通过 DatabaseAdapterFactory 创建数据库适配器
        3. 初始化 Vanna 实例（ChromaDB + OpenAI）
        4. 动态注入适配器的 run_sql 函数到 Vanna 实例
        5. 返回可用的 VannaBase 实例

    训练数据按数据库类型隔离：ChromaDB 路径为 ``./chromadb_data_{db_type}``，
    确保不同数据库的 DDL、文档和 SQL 示例存储在独立的向量空间中。

    Args:
        config: 应用配置对象。为 None 时自动从环境变量/YAML 加载。

    Returns:
        配置好的 VannaBase 实例，已注入 ``run_sql`` 函数，
        可直接调用 ``vn.ask()``, ``vn.generate_sql()``, ``vn.train()`` 等方法。

    Raises:
        ValueError: 数据库配置不完整或数据库类型不支持。
        ImportError: Vanna 库或数据库驱动未安装。
    """
    # 1. 加载配置
    if config is None:
        config = get_config()

    db_config = config.database
    llm_config = config.llm
    vs_config = config.vector_store

    logger.info(
        "初始化 Vanna 实例: db_type=%s, llm_model=%s, chromadb_path=%s",
        db_config.type,
        llm_config.model,
        vs_config.path,
    )

    # 2. 创建数据库适配器
    adapter: DatabaseAdapter = DatabaseAdapterFactory.create(
        db_type=db_config.type,
        config=db_config.to_dict(),
    )

    # 3. 构建 Vanna 配置字典
    vanna_config: dict = {
        # LLM 配置
        "model": llm_config.model,
        "api_key": llm_config.api_key,
        "base_url": llm_config.base_url,
        "temperature": llm_config.temperature,
        # 向量存储配置 — 按数据库类型隔离训练数据
        "path": vs_config.path,
        # SQL 方言 — 由适配器决定，影响 LLM 生成 SQL 的语法风格
        "dialect": adapter.dialect,
    }

    # 4. 创建 Vanna 实例
    vn = MultiDbVanna(config=vanna_config)

    # 5. 动态注入 run_sql 函数
    vn.run_sql = adapter.run_sql_func
    vn.run_sql_is_set = True

    logger.info(
        "Vanna 实例创建成功: dialect=%s, run_sql 已注入",
        adapter.dialect,
    )

    return vn


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------


def create_vanna_from_env(
    db_type: Optional[str] = None,
    db_host: Optional[str] = None,
    db_port: Optional[int] = None,
    db_name: Optional[str] = None,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
    llm_model: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    llm_base_url: Optional[str] = None,
    chromadb_path: Optional[str] = None,
) -> VannaBase:
    """从环境变量或显式参数创建 Vanna 实例。

    显式传入的参数优先于环境变量。未传入的参数从环境变量读取。

    Args:
        db_type: 数据库类型，默认读 DB_TYPE 环境变量。
        db_host: 数据库主机，默认读 DB_HOST 环境变量。
        db_port: 数据库端口，默认读 DB_PORT 环境变量。
        db_name: 数据库名，默认读 DB_NAME 环境变量。
        db_user: 数据库用户名，默认读 DB_USER 环境变量。
        db_password: 数据库密码，默认读 DB_PASSWORD 环境变量。
        llm_model: LLM 模型名，默认读 LLM_MODEL 环境变量。
        openai_api_key: OpenAI API Key，默认读 OPENAI_API_KEY 环境变量。
        llm_base_url: OpenAI 兼容 API 的 base URL，默认读 LLM_BASE_URL 环境变量。
            为空时使用 OpenAI 官方地址。
        chromadb_path: ChromaDB 路径，默认自动按数据库类型隔离。

    Returns:
        配置好的 VannaBase 实例。
    """
    from config import DatabaseConfig, LLMConfig, VectorStoreConfig

    # 确定数据库类型
    resolved_db_type = db_type or os.getenv("DB_TYPE", "postgresql")

    # 构建配置
    database = DatabaseConfig(
        type=resolved_db_type,
        host=db_host or os.getenv("DB_HOST", "127.0.0.1"),
        port=db_port or int(os.getenv("DB_PORT", "0") or "0"),
        name=db_name or os.getenv("DB_NAME", "postgres"),
        user=db_user or os.getenv("DB_USER", ""),
        password=db_password or os.getenv("DB_PASSWORD", ""),
    )

    llm = LLMConfig(
        model=llm_model or os.getenv("LLM_MODEL", "gpt-4"),
        api_key=openai_api_key or os.getenv("OPENAI_API_KEY", ""),
        base_url=llm_base_url or os.getenv("LLM_BASE_URL", ""),
    )

    # ChromaDB 路径：优先显式参数 > 环境变量 > 按类型自动隔离
    if chromadb_path:
        vs_path = chromadb_path
    elif os.getenv("CHROMADB_PATH"):
        vs_path = os.getenv("CHROMADB_PATH", "")
    else:
        vs_path = f"./chromadb_data_{resolved_db_type.lower()}"

    vector_store = VectorStoreConfig(path=vs_path)

    app_config = AppConfig(
        database=database,
        llm=llm,
        vector_store=vector_store,
    )

    return create_vanna(config=app_config)
