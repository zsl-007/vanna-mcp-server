"""
多数据库适配器工厂模块。

提供统一的 ``run_sql`` 接口，支持 MySQL、PostgreSQL、瀚高（HighGo SM3 国密认证）、
达梦（DM Oracle 兼容）四种数据库后端。

采用工厂模式 + 策略模式：``DatabaseAdapterFactory.create(db_type, config)``
根据数据库类型动态创建适配器，每个适配器封装各自的连接逻辑和方言信息。

接口契约:
    from database_adapter import DatabaseAdapterFactory, DatabaseAdapter
    adapter = DatabaseAdapterFactory.create(db_type, config)
    adapter.run_sql_func(sql)  # -> pd.DataFrame
    adapter.dialect             # -> str

使用示例:
    config = {"host": "127.0.0.1", "port": 5866, "database": "highgo",
              "user": "sysdba", "password": "***"}
    adapter = DatabaseAdapterFactory.create("highgo", config)
    df = adapter.run_sql_func("SELECT * FROM users LIMIT 10")
    print(adapter.dialect)  # "PostgreSQL"
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class DatabaseAdapter:
    """数据库适配器 — 封装 SQL 执行函数和方言信息。

    Attributes:
        run_sql_func: SQL 执行函数，签名为 ``(sql: str) -> pd.DataFrame``。
            SELECT 语句返回查询结果的 DataFrame；
            非 SELECT 语句返回包含 ``rows_affected`` 列的 DataFrame。
        dialect: SQL 方言标识（如 "MySQL", "PostgreSQL", "DM"），
            用于 Vanna LLM prompt 中告知模型当前数据库方言。
    """

    run_sql_func: Callable[[str], pd.DataFrame]
    dialect: str


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _is_select_query(sql: str) -> bool:
    """判断 SQL 语句是否为查询类型（返回结果集）。

    支持 SELECT、WITH (CTE)、EXPLAIN、SHOW、DESCRIBE 等语句。
    会去除前导注释和空白字符后再判断。

    Args:
        sql: SQL 语句字符串。

    Returns:
        True 如果该语句会返回结果集。
    """
    stripped = sql.strip()
    # 去除前导 SQL 注释（-- 单行和 /* */ 多行）
    while stripped.startswith("--"):
        newline_idx = stripped.find("\n")
        if newline_idx == -1:
            return False
        stripped = stripped[newline_idx:].strip()
    while stripped.startswith("/*"):
        end_idx = stripped.find("*/")
        if end_idx == -1:
            return False
        stripped = stripped[end_idx + 2:].strip()

    upper = stripped.upper()
    select_keywords = ("SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC")
    return any(upper.startswith(kw) for kw in select_keywords)


def _safe_close(cursor: Any, conn: Any) -> None:
    """安全关闭游标和连接，忽略关闭时的异常。

    Args:
        cursor: 数据库游标对象（可为 None）。
        conn: 数据库连接对象（可为 None）。
    """
    try:
        if cursor is not None:
            cursor.close()
    except Exception as e:
        logger.warning("关闭游标时出错: %s", e)
    try:
        if conn is not None:
            conn.close()
    except Exception as e:
        logger.warning("关闭连接时出错: %s", e)


# ---------------------------------------------------------------------------
# 数据库适配器工厂
# ---------------------------------------------------------------------------


class DatabaseAdapterFactory:
    """数据库适配器工厂 — 根据数据库类型和配置创建适配器。

    支持的数据库类型:
        - ``mysql``: MySQL，使用 PyMySQL 驱动
        - ``postgresql``: PostgreSQL，使用 psycopg2 驱动
        - ``highgo``: 瀚高数据库，使用瀚高定制版 psycopg2 驱动（SM3 国密认证）
        - ``dm``: 达梦数据库，使用 dmPython 驱动（Oracle 兼容方言）
    """

    # 数据库类型 → 默认端口映射
    DEFAULT_PORTS: Dict[str, int] = {
        "mysql": 3306,
        "postgresql": 5432,
        "highgo": 5866,
        "dm": 5236,
    }

    @staticmethod
    def create(db_type: str, config: dict) -> DatabaseAdapter:
        """根据数据库类型和配置创建适配器。

        Args:
            db_type: 数据库类型（不区分大小写），
                可选值: mysql / postgresql / highgo / dm。
            config: 连接配置字典，必须包含以下键:
                - ``host``: 主机地址
                - ``port``: 端口号（可由工厂补全默认值）
                - ``database``: 数据库名
                - ``user``: 用户名
                - ``password``: 密码

        Returns:
            DatabaseAdapter 实例，包含 ``run_sql_func`` 和 ``dialect``。

        Raises:
            ValueError: 不支持的数据库类型，或配置缺少必需字段。
        """
        db_type_lower = db_type.lower().strip()
        creators = {
            "mysql": DatabaseAdapterFactory._create_mysql,
            "postgresql": DatabaseAdapterFactory._create_postgresql,
            "highgo": DatabaseAdapterFactory._create_highgo,
            "dm": DatabaseAdapterFactory._create_dm,
        }

        creator = creators.get(db_type_lower)
        if creator is None:
            supported = ", ".join(creators.keys())
            raise ValueError(
                f"不支持的数据库类型: '{db_type}'。"
                f"支持的类型: {supported}"
            )

        # 补全默认端口
        if not config.get("port"):
            config["port"] = DatabaseAdapterFactory.DEFAULT_PORTS.get(
                db_type_lower, 5432
            )

        logger.info(
            "创建数据库适配器: type=%s, host=%s, port=%s, database=%s",
            db_type_lower,
            config.get("host"),
            config.get("port"),
            config.get("database"),
        )

        return creator(config)

    # ------------------------------------------------------------------
    # MySQL 适配器
    # ------------------------------------------------------------------

    @staticmethod
    def _create_mysql(config: dict) -> DatabaseAdapter:
        """创建 MySQL 数据库适配器。

        使用 PyMySQL 驱动，DictCursor 游标，默认端口 3306。

        Args:
            config: 连接配置字典。

        Returns:
            DatabaseAdapter 实例，方言为 "MySQL"。
        """
        import pymysql

        def run_sql(sql: str) -> pd.DataFrame:
            """执行 MySQL SQL 语句。

            Args:
                sql: SQL 语句字符串。

            Returns:
                SELECT 语句返回查询结果 DataFrame；
                非 SELECT 语句返回 ``pd.DataFrame({"rows_affected": [N]})``。
            """
            conn = None
            cursor = None
            try:
                conn = pymysql.connect(
                    host=config["host"],
                    port=int(config.get("port", 3306)),
                    database=config["database"],
                    user=config["user"],
                    password=config["password"],
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                )
                cursor = conn.cursor()
                cursor.execute(sql)

                if _is_select_query(sql):
                    rows = cursor.fetchall()
                    return pd.DataFrame(rows)
                else:
                    conn.commit()
                    return pd.DataFrame({"rows_affected": [cursor.rowcount]})
            finally:
                _safe_close(cursor, conn)

        return DatabaseAdapter(run_sql_func=run_sql, dialect="MySQL")

    # ------------------------------------------------------------------
    # PostgreSQL 适配器
    # ------------------------------------------------------------------

    @staticmethod
    def _create_postgresql(config: dict) -> DatabaseAdapter:
        """创建 PostgreSQL 数据库适配器。

        使用 PyPI 标准 psycopg2 驱动，RealDictCursor 游标，默认端口 5432。

        Args:
            config: 连接配置字典。

        Returns:
            DatabaseAdapter 实例，方言为 "PostgreSQL"。
        """
        import psycopg2
        import psycopg2.extras

        def run_sql(sql: str) -> pd.DataFrame:
            """执行 PostgreSQL SQL 语句。

            Args:
                sql: SQL 语句字符串。

            Returns:
                SELECT 语句返回查询结果 DataFrame；
                非 SELECT 语句返回 ``pd.DataFrame({"rows_affected": [N]})``。
            """
            conn = None
            cursor = None
            try:
                conn = psycopg2.connect(
                    host=config["host"],
                    port=int(config.get("port", 5432)),
                    dbname=config["database"],
                    user=config["user"],
                    password=config["password"],
                )
                cursor = conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor
                )
                cursor.execute(sql)

                if _is_select_query(sql):
                    rows = cursor.fetchall()
                    return pd.DataFrame([dict(r) for r in rows])
                else:
                    conn.commit()
                    return pd.DataFrame({"rows_affected": [cursor.rowcount]})
            finally:
                _safe_close(cursor, conn)

        return DatabaseAdapter(run_sql_func=run_sql, dialect="PostgreSQL")

    # ------------------------------------------------------------------
    # 瀚高 (HighGo) 适配器 — SM3 国密认证
    # ------------------------------------------------------------------

    @staticmethod
    def _create_highgo(config: dict) -> DatabaseAdapter:
        """创建瀚高数据库适配器。

        .. important::
            必须使用 **瀚高定制版 psycopg2**（非 PyPI 标准版 ``psycopg2-binary``）。
            定制版链接瀚高定制版 libpq，在 C 层面实现了 SM3 认证握手协议。
            标准 PyPI 版 psycopg2 链接标准 libpq，不支持 SM3 认证方法，
            连接时会被服务端拒绝。

            SM3 认证由瀚高定制版 libpq 在 C 层自动处理，对 Python 代码透明。
            无需在 ``psycopg2.connect()`` 中添加任何 SM3 相关参数。

        瀚高默认端口 5866，默认用户 sysdba，SQL 方言兼容 PostgreSQL。

        安装方式:
            1. 从瀚高官方下载定制版 psycopg2（百度网盘或瀚高技术支持平台）
            2. 将 psycopg2 包放入 Python site-packages 目录
            3. 配置瀚高定制版 libpq.so.5 到系统库路径
            4. 设置 ``LD_LIBRARY_PATH`` 指向瀚高 libpq 所在目录

        Docker 部署:
            COPY highgo-drivers/psycopg2 /usr/local/lib/python3.x/site-packages/psycopg2
            COPY highgo-drivers/libpq.so.5 /usr/lib/
            ENV LD_LIBRARY_PATH=/usr/lib:$LD_LIBRARY_PATH

        Args:
            config: 连接配置字典。``user`` 未设置时默认为 ``sysdba``。

        Returns:
            DatabaseAdapter 实例，方言为 "PostgreSQL"。
        """
        # 注意：此处的 psycopg2 必须是瀚高定制版，而非 PyPI 标准版。
        # SM3 认证由定制版 libpq 在 C 层自动处理，Python 代码无需额外干预。
        import psycopg2
        import psycopg2.extras

        # 瀚高默认用户为 sysdba（非 PostgreSQL 的 postgres）
        user = config.get("user") or "sysdba"

        def run_sql(sql: str) -> pd.DataFrame:
            """执行瀚高 SQL 语句（SM3 认证由 libpq 自动处理）。

            Args:
                sql: SQL 语句字符串（PostgreSQL 兼容语法）。

            Returns:
                SELECT 语句返回查询结果 DataFrame；
                非 SELECT 语句返回 ``pd.DataFrame({"rows_affected": [N]})``。
            """
            conn = None
            cursor = None
            try:
                conn = psycopg2.connect(
                    host=config["host"],
                    port=int(config.get("port", 5866)),
                    dbname=config["database"],
                    user=user,
                    password=config["password"],
                    # SM3 认证由瀚高定制版 libpq 在 C 层自动处理
                    # 无需在 Python 层面添加任何认证相关参数
                )
                cursor = conn.cursor(
                    cursor_factory=psycopg2.extras.RealDictCursor
                )
                cursor.execute(sql)

                if _is_select_query(sql):
                    rows = cursor.fetchall()
                    return pd.DataFrame([dict(r) for r in rows])
                else:
                    conn.commit()
                    return pd.DataFrame({"rows_affected": [cursor.rowcount]})
            finally:
                _safe_close(cursor, conn)

        return DatabaseAdapter(run_sql_func=run_sql, dialect="PostgreSQL")

    # ------------------------------------------------------------------
    # 达梦 (DM) 适配器 — Oracle 兼容方言
    # ------------------------------------------------------------------

    @staticmethod
    def _create_dm(config: dict) -> DatabaseAdapter:
        """创建达梦数据库适配器。

        使用 dmPython 驱动（PyPI 包名 ``dmpython``，导入名 ``dmPython``），
        默认端口 5236，默认用户 SYSDBA。

        .. warning::
            达梦数据库是 **Oracle 兼容**数据库，不是 PostgreSQL 兼容！
            关键方言差异:
                - 伪表: 使用 ``DUAL``（Oracle 风格），而非 PostgreSQL 的无 FROM
                - 分页: 使用 ``ROWNUM`` 或 ``LIMIT``（DM8 扩展支持）
                - 自增列: 使用 ``IDENTITY``（Oracle 风格），而非 ``SERIAL``
                - 函数名: ``SUBSTR`` 而非 ``SUBSTRING``（Oracle 风格）
                - 数据字典: ``ALL_TABLES`` / ``USER_TABLES`` 而非 ``information_schema``
                - 大小写: 默认大写敏感（Oracle 风格）

            Vanna 的 ``dialect`` 设置为 "DM"，LLM 会据此生成 Oracle 兼容 SQL。

        安装方式:
            pip install dmPython  # whl 包内置 DPI 运行时，无需额外配置

        Args:
            config: 连接配置字典。``user`` 未设置时默认为 ``SYSDBA``。

        Returns:
            DatabaseAdapter 实例，方言为 "DM"。
        """
        import dmPython

        # 达梦默认用户为 SYSDBA（大写，Oracle 风格）
        user = config.get("user") or "SYSDBA"

        def run_sql(sql: str) -> pd.DataFrame:
            """执行达梦 SQL 语句（Oracle 兼容方言）。

            Args:
                sql: SQL 语句字符串（Oracle 兼容语法）。

            Returns:
                SELECT 语句返回查询结果 DataFrame；
                非 SELECT 语句返回 ``pd.DataFrame({"rows_affected": [N]})``。
            """
            conn = None
            cursor = None
            try:
                conn = dmPython.connect(
                    user=user,
                    password=config["password"],
                    server=config["host"],
                    port=int(config.get("port", 5236)),
                )
                cursor = conn.cursor()
                cursor.execute(sql)

                if _is_select_query(sql):
                    # dmPython 游标返回 tuple，需从 description 提取列名
                    columns = (
                        [desc[0] for desc in cursor.description]
                        if cursor.description
                        else []
                    )
                    rows = cursor.fetchall()
                    return pd.DataFrame(rows, columns=columns)
                else:
                    conn.commit()
                    return pd.DataFrame({"rows_affected": [cursor.rowcount]})
            finally:
                _safe_close(cursor, conn)

        return DatabaseAdapter(run_sql_func=run_sql, dialect="DM")
