#!/usr/bin/env python3
"""
Vanna MCP Server — 基于 FastMCP 的智能问数 MCP 服务
=====================================================

将 Vanna 的自然语言转 SQL（NL2SQL）能力封装为 MCP (Model Context Protocol) 工具，
支持 HTTP streamable-http 传输，供 Hermes-Agent 等 MCP 客户端调用。

架构概述:
    ┌──────────────────────────────────────────────────────┐
    │              Starlette ASGI 应用                      │
    │  ┌────────────────────────────────────────────────┐  │
    │  │  CORSMiddleware (暴露 Mcp-Session-Id)          │  │
    │  │  ┌──────────────────────────────────────────┐  │  │
    │  │  │  Mount("/vanna") → mcp.streamable_http   │  │  │
    │  │  │  ┌────────────────────────────────────┐  │  │  │
    │  │  │  │  FastMCP("VannaMCP")              │  │  │  │
    │  │  │  │  ├── 9 个 @mcp.tool()             │  │  │  │
    │  │  │  │  ├── 1 个 @mcp.resource()         │  │  │  │
    │  │  │  │  └── stateless_http + json_resp   │  │  │  │
    │  │  │  └────────────────────────────────────┘  │  │  │
    │  │  └──────────────────────────────────────────┘  │  │
    │  └────────────────────────────────────────────────┘  │
    │  lifespan: 创建/销毁 Vanna 全局实例                   │
    └──────────────────────────────────────────────────────┘

MCP 端点: http://<host>:<port>/vanna/mcp

启动方式:
    方式1 (开发):  python server.py
    方式2 (生产):  uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
    方式3 (脚本):  ./run.sh

依赖:
    - mcp (MCP Python SDK, FastMCP)
    - starlette (ASGI 框架)
    - uvicorn (ASGI 服务器)
    - vanna (Vanna NL2SQL 库)
    - pandas (数据处理)
    - vanna_instance.py (backend-dev-1 提供, 导出 create_vanna())
    - database_adapter.py (backend-dev-1 提供, 导出 DatabaseAdapterFactory)
    - config.py (backend-dev-1 提供, 配置管理: AppConfig / get_config())

配置:
    所有 MCP Server 配置 (host/port/stateless/json_response/max_request_body_size)
    通过 config.py 的 MCPConfig 统一管理, 支持环境变量和 YAML 两种方式。
    详见 config.py 中的 EXAMPLE_YAML 和 EXAMPLE_ENV。

作者: backend-dev-2
日期: 2026-09-23
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import traceback
from typing import Any, Optional

# ============================================================================
# 日志配置
# ============================================================================
logger = logging.getLogger("vanna-mcp-server")
logger.setLevel(logging.INFO)

if not logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(_handler)

logger.info("Initializing Vanna MCP Server...")

# ============================================================================
# Vanna 实例导入
# ============================================================================
# 从 backend-dev-1 的 vanna_instance.py 导入 create_vanna()
# 该函数负责:
#   1. 创建 ChromaDB_VectorStore + OpenAI_Chat 混合 Vanna 实例
#   2. 通过 DatabaseAdapterFactory 动态注入 run_sql 函数
#   3. 配置 LLM (模型、API Key)
#   4. 连接目标数据库 (瀚高/PostgreSQL/MySQL/达梦)
#
# 如果 backend-dev-1 尚未交付 vanna_instance.py, 此导入会失败。
# 可设置 VANNA_MOCK_MODE=1 以使用模拟实例进行接口测试。
VANNA_MOCK_MODE = os.getenv("VANNA_MOCK_MODE", "0") == "1"

if not VANNA_MOCK_MODE:
    try:
        from vanna_instance import create_vanna
        from config import get_config, AppConfig
        logger.info("Successfully imported create_vanna and config from backend-dev-1 modules")
    except ImportError as e:
        logger.error(
            "Failed to import backend-dev-1 modules: %s. "
            "Set VANNA_MOCK_MODE=1 for interface testing without Vanna.",
            e,
        )
        raise
else:
    logger.warning("VANNA_MOCK_MODE=1 — using mock Vanna instance for testing")

    def create_vanna():  # type: ignore[no-redef]
        """Mock Vanna instance for interface testing."""
        import types

        mock = types.SimpleNamespace()
        mock.dialect = "PostgreSQL"
        mock.run_sql_is_set = True

        def _ask(question, allow_llm_to_see_data=False):
            import pandas as pd
            sql = f"-- Mock SQL for: {question}"
            df = pd.DataFrame({"message": ["Mock mode — Vanna not initialized"]})
            return sql, df, None

        def _generate_sql(question):
            return f"SELECT 1; -- Mock SQL for: {question}"

        def _run_sql(sql):
            import pandas as pd
            return pd.DataFrame({"result": ["Mock mode"]})

        def _train(question=None, sql=None, ddl=None, documentation=None):
            return "mock-training-id-0001"

        def _get_training_data():
            import pandas as pd
            return pd.DataFrame(
                [{"id": "mock-001", "training_data_type": "sql", "question": "mock", "sql": "SELECT 1"}]
            )

        def _remove_training_data(id):
            return True

        def _generate_question(sql):
            return "What does this SQL query do?"

        mock.ask = _ask
        mock.generate_sql = _generate_sql
        mock.run_sql = _run_sql
        mock.train = _train
        mock.get_training_data = _get_training_data
        mock.remove_training_data = _remove_training_data
        mock.generate_question = _generate_question
        return mock

    # Mock config for testing
    from dataclasses import dataclass

    @dataclass
    class _MockMCPConfig:
        host: str = "0.0.0.0"
        port: int = 8000
        transport: str = "streamable-http"
        stateless: bool = True
        json_response: bool = True
        max_request_body_size: int = 8 * 1024 * 1024

    @dataclass
    class _MockAppConfig:
        mcp: _MockMCPConfig = _MockMCPConfig()

    def get_config() -> _MockAppConfig:  # type: ignore[no-redef]
        return _MockAppConfig()

    AppConfig = _MockAppConfig  # type: ignore[assignment, misc]


# ============================================================================
# 全局 Vanna 实例持有器
# ============================================================================
# Vanna 实例在应用启动时通过 lifespan 全局创建, 所有请求共享。
# 在 stateless_http 模式下, 每个请求独立处理, 但共享同一个 Vanna 实例
# (包括 ChromaDB 向量存储和训练数据)。
_vanna_instance: Optional[Any] = None


def get_vanna() -> Any:
    """获取全局 Vanna 实例。

    Vanna 实例在应用启动时通过 lifespan 创建。
    如果实例未初始化, 抛出 RuntimeError。

    Returns:
        Vanna 实例 (VannaBase 子类)

    Raises:
        RuntimeError: Vanna 实例未初始化
    """
    if _vanna_instance is None:
        raise RuntimeError(
            "Vanna instance not initialized. "
            "Ensure the server has started properly via lifespan."
        )
    return _vanna_instance


def _format_error(error: Exception) -> str:
    """格式化错误信息为 JSON 字符串。

    Args:
        error: 捕获的异常

    Returns:
        JSON 格式的错误信息, 包含 error_type 和 error_message
    """
    return json.dumps(
        {
            "error": str(error),
            "error_type": type(error).__name__,
        },
        ensure_ascii=False,
    )


# ============================================================================
# MCP Server 创建
# ============================================================================
from mcp.server.fastmcp import FastMCP

# 从 config.py 加载 MCP 配置 (backend-dev-1 提供)
# 支持环境变量和 YAML 两种配置方式, 优先级: YAML > 环境变量 > 默认值
_app_config = get_config()
_mcp_config = _app_config.mcp

# 推荐生产配置: 无状态 HTTP + JSON 响应
# - stateless_http=True: 每个请求独立处理, 无会话状态, 支持水平扩展
# - json_response=True: 使用 JSON 响应而非 SSE 流式, 简化客户端处理
# - max_request_body_size=8MB: 支持大 DDL 训练数据传输
# - streamable_http_path="/mcp": MCP 端点路径 (挂载到 /vanna 后完整路径为 /vanna/mcp)
mcp = FastMCP(
    name="VannaMCP",
    stateless_http=_mcp_config.stateless,
    json_response=_mcp_config.json_response,
    host=_mcp_config.host,
    port=_mcp_config.port,
    streamable_http_path="/mcp",
    max_request_body_size=_mcp_config.max_request_body_size,
)

logger.info(
    "FastMCP configured: stateless_http=%s, json_response=%s, "
    "host=%s, port=%d, max_request_body_size=%dMB, streamable_http_path=/mcp",
    _mcp_config.stateless,
    _mcp_config.json_response,
    _mcp_config.host,
    _mcp_config.port,
    _mcp_config.max_request_body_size // (1024 * 1024),
)


# ============================================================================
# MCP Tools — 9 个工具
# ============================================================================

@mcp.tool()
def ask(question: str, allow_llm_to_see_data: bool = False) -> str:
    """Ask a question in natural language and get SQL + query results.

    This is the primary entry point for intelligent data querying.
    It generates SQL from the question, executes it, and optionally
    auto-trains Vanna with the result.

    Args:
        question: Natural language question about your data.
            Example: "What are the top 10 customers by sales?"
        allow_llm_to_see_data: If True, allows the LLM to introspect
            query results for generating more complex SQL. Use with
            caution for sensitive data. Defaults to False.

    Returns:
        JSON string with the following structure:
        {
            "sql": "SELECT ...",           // Generated SQL query
            "results": "| col1 | col2 |",  // Query results as markdown table
            "row_count": 42                // Number of rows returned
        }
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'ask' called: question='%s', allow_llm_to_see_data=%s", question, allow_llm_to_see_data)
    try:
        vn = get_vanna()
        sql, df, fig = vn.ask(question, allow_llm_to_see_data=allow_llm_to_see_data)

        result = {
            "sql": sql,
            "results": df.to_markdown() if df is not None else None,
            "row_count": len(df) if df is not None else 0,
        }
        logger.info("Tool 'ask' succeeded: sql=%s, row_count=%s", sql[:80] if sql else "None", result["row_count"])
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error("Tool 'ask' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def generate_sql(question: str) -> str:
    """Generate a SQL query from a natural language question.

    Uses Vanna's RAG (Retrieval-Augmented Generation) pipeline:
    1. Retrieves similar question-SQL pairs from training data
    2. Retrieves relevant DDL (table structures)
    3. Retrieves relevant documentation
    4. Constructs a prompt and calls the LLM to generate SQL

    Note: This only generates SQL without executing it. Use 'run_sql'
    to execute, or 'ask' for the complete flow.

    Args:
        question: Natural language question.
            Example: "Show me monthly revenue for 2024"

    Returns:
        SQL query string (e.g., "SELECT SUM(amount) FROM orders WHERE ...")
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'generate_sql' called: question='%s'", question)
    try:
        vn = get_vanna()
        sql = vn.generate_sql(question=question)
        logger.info("Tool 'generate_sql' succeeded: sql=%s", sql[:80] if sql else "None")
        return sql
    except Exception as e:
        logger.error("Tool 'generate_sql' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def run_sql(sql: str) -> str:
    """Execute a SQL query and return results as a markdown table.

    Executes the given SQL against the configured database (瀚高/PostgreSQL/
    MySQL/达梦) and returns the results formatted as a markdown table.

    Only SELECT queries return data; non-SELECT queries (INSERT, UPDATE,
    DELETE, etc.) return the number of affected rows.

    Args:
        sql: SQL query to execute.
            Example: "SELECT * FROM customers LIMIT 10"

    Returns:
        Query results as a markdown table string.
        For non-SELECT queries, returns a table with rows_affected count.
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'run_sql' called: sql=%s", sql[:80] if sql else "None")
    try:
        vn = get_vanna()
        df = vn.run_sql(sql)
        if df is not None:
            result = df.to_markdown()
        else:
            result = "Query executed. No results returned."
        logger.info("Tool 'run_sql' succeeded: rows=%d", len(df) if df is not None else 0)
        return result
    except Exception as e:
        logger.error("Tool 'run_sql' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def train_sql(question: str, sql: str) -> str:
    """Train Vanna with a question-SQL pair for better RAG.

    Adds the question-SQL pair to Vanna's ChromaDB vector store.
    Future queries with similar questions will retrieve this example
    to improve SQL generation quality.

    Args:
        question: Natural language question.
            Example: "What is the total revenue by month?"
        sql: The correct SQL query for the question.
            Example: "SELECT DATE_TRUNC('month', order_date), SUM(amount) FROM orders GROUP BY 1"

    Returns:
        Training data ID (e.g., "abc123-sql"). This ID can be used with
        remove_training_data to delete this training entry.
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'train_sql' called: question='%s', sql=%s", question, sql[:80] if sql else "None")
    try:
        vn = get_vanna()
        training_id = vn.train(question=question, sql=sql)
        logger.info("Tool 'train_sql' succeeded: id=%s", training_id)
        return training_id
    except Exception as e:
        logger.error("Tool 'train_sql' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def train_ddl(ddl: str) -> str:
    """Train Vanna with DDL (Data Definition Language) statements.

    Provides Vanna with table structure information. DDL statements
    (CREATE TABLE, CREATE VIEW, etc.) help the LLM understand the
    database schema for generating accurate SQL.

    Args:
        ddl: DDL statement string.
            Example: "CREATE TABLE customers (id SERIAL PRIMARY KEY, name VARCHAR(100), email VARCHAR(255))"

    Returns:
        Training data ID (e.g., "def456-ddl").
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'train_ddl' called: ddl_length=%d", len(ddl) if ddl else 0)
    try:
        vn = get_vanna()
        training_id = vn.train(ddl=ddl)
        logger.info("Tool 'train_ddl' succeeded: id=%s", training_id)
        return training_id
    except Exception as e:
        logger.error("Tool 'train_ddl' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def train_documentation(documentation: str) -> str:
    """Train Vanna with documentation text.

    Provides Vanna with business context, table descriptions, data
    dictionaries, or any textual information that helps the LLM
    understand the data better.

    Args:
        documentation: Documentation text about tables, business rules, etc.
            Example: "The customers table contains all registered users. The status column can be 'active', 'inactive', or 'suspended'."

    Returns:
        Training data ID (e.g., "ghi789-doc").
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'train_documentation' called: doc_length=%d", len(documentation) if documentation else 0)
    try:
        vn = get_vanna()
        training_id = vn.train(documentation=documentation)
        logger.info("Tool 'train_documentation' succeeded: id=%s", training_id)
        return training_id
    except Exception as e:
        logger.error("Tool 'train_documentation' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def get_training_data() -> str:
    """Get all training data (DDL, documentation, question-SQL pairs).

    Retrieves all training data stored in Vanna's ChromaDB vector store,
    including DDL statements, documentation, and question-SQL pairs.

    Args:
        No parameters required.

    Returns:
        JSON string array of training data entries. Each entry contains:
        - id: Training data ID (e.g., "abc123-sql")
        - training_data_type: Type ("sql", "ddl", or "documentation")
        - question: The question (for SQL pairs)
        - sql: The SQL query (for SQL pairs)
        - ddl: The DDL statement (for DDL entries)
        - documentation: The documentation text (for documentation entries)
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'get_training_data' called")
    try:
        vn = get_vanna()
        df = vn.get_training_data()
        if df is not None:
            result = df.to_json(orient="records")
        else:
            result = "[]"
        logger.info("Tool 'get_training_data' succeeded: entries=%d", len(df) if df is not None else 0)
        return result
    except Exception as e:
        logger.error("Tool 'get_training_data' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def remove_training_data(id: str) -> str:
    """Remove training data by ID.

    Deletes a specific training data entry from Vanna's ChromaDB vector store.

    Args:
        id: Training data ID to remove.
            IDs can be obtained from get_training_data.
            Example: "abc123-sql", "def456-ddl", "ghi789-doc"

    Returns:
        "true" if the training data was successfully removed, "false" otherwise.
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'remove_training_data' called: id=%s", id)
    try:
        vn = get_vanna()
        success = vn.remove_training_data(id=id)
        logger.info("Tool 'remove_training_data' succeeded: id=%s, removed=%s", id, success)
        return str(success).lower()
    except Exception as e:
        logger.error("Tool 'remove_training_data' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


@mcp.tool()
def generate_question(sql: str) -> str:
    """Generate a natural language question from a SQL query.

    Reverse-engineers a natural language question from a SQL query.
    Useful for auto-generating training data from existing SQL queries.

    Args:
        sql: SQL query to generate a question for.
            Example: "SELECT COUNT(*) FROM orders WHERE status = 'pending'"

    Returns:
        Generated question string.
            Example: "How many pending orders are there?"
        On error, returns: {"error": "...", "error_type": "..."}
    """
    logger.info("Tool 'generate_question' called: sql=%s", sql[:80] if sql else "None")
    try:
        vn = get_vanna()
        question = vn.generate_question(sql=sql)
        logger.info("Tool 'generate_question' succeeded: question=%s", question)
        return question
    except Exception as e:
        logger.error("Tool 'generate_question' failed: %s\n%s", e, traceback.format_exc())
        return _format_error(e)


# ============================================================================
# MCP Resources
# ============================================================================

@mcp.resource("vanna://training-data")
def training_data_resource() -> str:
    """All training data as JSON.

    Returns all training data (DDL, documentation, question-SQL pairs)
    stored in Vanna's ChromaDB vector store as a JSON string.

    This resource allows MCP clients to inspect the current training
    data without calling the get_training_data tool.

    Returns:
        JSON string array of all training data entries.
    """
    logger.info("Resource 'vanna://training-data' accessed")
    try:
        vn = get_vanna()
        df = vn.get_training_data()
        if df is not None:
            return df.to_json(orient="records")
        return "[]"
    except Exception as e:
        logger.error("Resource 'vanna://training-data' failed: %s", e)
        return _format_error(e)


# ============================================================================
# Starlette ASGI 应用 + Lifespan 管理
# ============================================================================
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.middleware.cors import CORSMiddleware


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """管理 Vanna 实例和 MCP 会话的生命周期。

    在应用启动时:
        1. 创建全局 Vanna 实例 (含数据库连接、ChromaDB 初始化、LLM 配置)
        2. 启动 MCP 会话管理器

    在应用关闭时:
        1. 停止 MCP 会话管理器
        2. 清理 Vanna 实例引用

    Args:
        app: Starlette ASGI 应用实例
    """
    global _vanna_instance

    logger.info("Lifespan: creating Vanna instance...")
    try:
        # create_vanna() 默认从 get_config() 加载配置 (环境变量 / YAML)
        # 也可传入显式 AppConfig: create_vanna(config=app_config)
        _vanna_instance = create_vanna()
        logger.info(
            "Lifespan: Vanna instance created successfully (dialect=%s, run_sql_is_set=%s)",
            getattr(_vanna_instance, "dialect", "unknown"),
            getattr(_vanna_instance, "run_sql_is_set", False),
        )
    except Exception as e:
        logger.error("Lifespan: failed to create Vanna instance: %s\n%s", e, traceback.format_exc())
        raise

    # 启动 MCP 会话管理器 (即使在 stateless 模式下也需要初始化)
    async with mcp.session_manager.run():
        logger.info("Lifespan: MCP session manager started")
        yield
        logger.info("Lifespan: shutting down...")

    # 清理
    _vanna_instance = None
    logger.info("Lifespan: Vanna instance cleaned up")


# 创建 Starlette ASGI 应用
# MCP 端点挂载到 /vanna 路径, 内部 streamable_http_path=/mcp
# 完整端点: http://<host>:<port>/vanna/mcp
app = Starlette(
    routes=[
        Mount("/vanna", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)

# CORS 中间件配置 — 允许浏览器客户端访问
# 必须暴露 Mcp-Session-Id 头部, 否则 MCP 客户端无法获取会话 ID
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 生产环境应限制为具体域名
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Mcp-Session-Id"],
)

logger.info("Starlette ASGI app configured: mount=/vanna, endpoint=/vanna/mcp, CORS=enabled")


# ============================================================================
# 启动入口
# ============================================================================
if __name__ == "__main__":
    # 方式1: 直接运行 (开发模式)
    # python server.py
    #
    # 方式2: uvicorn (生产模式, 推荐在 run.sh 中使用)
    # uvicorn server:app --host 0.0.0.0 --port 8000 --workers 4
    #
    # 方式3: mcp.run (最简模式, 不含 Starlette 挂载)
    # 取消下面注释即可使用
    import uvicorn

    # 使用 config.py 的 MCPConfig (与环境变量兼容)
    host = _mcp_config.host
    port = _mcp_config.port
    workers = int(os.getenv("MCP_WORKERS", "1"))

    logger.info("Starting uvicorn: host=%s, port=%d, workers=%d", host, port, workers)

    if workers > 1:
        # 多 worker 模式 — 使用 uvicorn.run 的字符串引用方式
        uvicorn.run(
            "server:app",
            host=host,
            port=port,
            workers=workers,
            log_level="info",
        )
    else:
        # 单 worker 模式 — 直接传入 app 对象 (支持热重载)
        uvicorn.run(
            app,
            host=host,
            port=port,
            log_level="info",
        )
