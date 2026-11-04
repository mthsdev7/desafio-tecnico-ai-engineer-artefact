import csv
import io
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool


_INTEGER_COLUMNS = {"quantity", "stock_quantity", "is_active"}
_REAL_COLUMNS = {"price_brl", "total_brl", "discount_percent"}
_PRIVATE_ORDER_COLUMNS = {"customer_id", "tracking_code", "notes"}
_TABLE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str | None]]]:
    text = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue

    if text is None:
        raise UnicodeError(f"Não foi possível decodificar o arquivo CSV: {path.name}")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError(f"O CSV não possui cabeçalho: {path.name}")

    original_columns = reader.fieldnames
    columns = [column.strip() for column in original_columns]
    if any(not _TABLE_PATTERN.fullmatch(column) for column in columns):
        raise ValueError(f"O CSV possui nome de coluna inválido: {path.name}")
    if len(set(columns)) != len(columns):
        raise ValueError(f"O CSV possui nomes de coluna duplicados: {path.name}")

    rows = []
    for line_number, row in enumerate(reader, start=2):
        if None in row:
            raise ValueError(
                f"Linha {line_number} possui mais valores que colunas em {path.name}"
            )
        rows.append(
            {
                column: row.get(original_column)
                for original_column, column in zip(original_columns, columns)
            }
        )

    return columns, rows


def _table_name(path: Path) -> str:
    name = path.stem.rsplit(" - ", maxsplit=1)[-1].strip().lower()
    if not _TABLE_PATTERN.fullmatch(name):
        raise ValueError(f"Não foi possível determinar o nome da tabela: {path.name}")
    return name


def _column_type(column: str) -> str:
    if column.endswith("_id") or column in _INTEGER_COLUMNS:
        return "INTEGER"
    if column in _REAL_COLUMNS:
        return "REAL"
    return "TEXT"


def _convert_value(value: str | None, column_type: str, path: Path) -> Any:
    if value is None or value.strip() == "":
        return None
    if column_type == "INTEGER":
        try:
            return int(value)
        except ValueError as error:
            raise ValueError(f"Valor inteiro inválido em {path.name}: {value!r}") from error
    if column_type == "REAL":
        try:
            return float(value)
        except ValueError as error:
            raise ValueError(f"Valor numérico inválido em {path.name}: {value!r}") from error
    return value


def _has_unparenthesized_or(statement: str) -> bool:
    depth = 0
    index = 0
    quote: str | None = None

    while index < len(statement):
        character = statement[index]

        if quote is not None:
            if character == quote:
                if index + 1 < len(statement) and statement[index + 1] == quote:
                    index += 2
                    continue
                quote = None
            index += 1
            continue

        if statement.startswith("--", index):
            newline = statement.find("\n", index + 2)
            index = len(statement) if newline == -1 else newline + 1
            continue
        if statement.startswith("/*", index):
            comment_end = statement.find("*/", index + 2)
            index = len(statement) if comment_end == -1 else comment_end + 2
            continue
        if character in {"'", '"', "`"}:
            quote = character
            index += 1
            continue
        if character == "[":
            bracket_end = statement.find("]", index + 1)
            index = len(statement) if bracket_end == -1 else bracket_end + 1
            continue
        if character == "(":
            depth += 1
            index += 1
            continue
        if character == ")":
            depth = max(0, depth - 1)
            index += 1
            continue

        if depth == 0 and statement[index:index + 2].lower() == "or":
            before = statement[index - 1] if index > 0 else " "
            after = statement[index + 2] if index + 2 < len(statement) else " "
            if not (before.isalnum() or before == "_") and not (
                after.isalnum() or after == "_"
            ):
                return True

        index += 1

    return False


def build_csv_database(data_directory: Path) -> Path:
    csv_files = sorted(data_directory.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"Nenhum CSV encontrado em {data_directory}")

    tables = [_table_name(path) for path in csv_files]
    if len(set(tables)) != len(tables):
        raise ValueError("Há mais de um CSV mapeado para o mesmo nome de tabela.")

    database_path = data_directory / "loja.sqlite3"
    temp_handle, temp_name = tempfile.mkstemp(
        prefix="loja-",
        suffix=".sqlite3",
        dir=data_directory,
    )
    os.close(temp_handle)
    temporary_database = Path(temp_name)

    try:
        with closing(sqlite3.connect(temporary_database)) as connection:
            with connection:
                for path, table in zip(csv_files, tables):
                    columns, rows = _read_csv(path)
                    column_types = {column: _column_type(column) for column in columns}
                    schema = ", ".join(
                        f'"{column}" {column_types[column]}' for column in columns
                    )
                    connection.execute(f'CREATE TABLE "{table}" ({schema})')

                    if rows:
                        placeholders = ", ".join("?" for _ in columns)
                        insert = f'INSERT INTO "{table}" VALUES ({placeholders})'
                        values = [
                            tuple(
                                _convert_value(row[column], column_types[column], path)
                                for column in columns
                            )
                            for row in rows
                        ]
                        connection.executemany(insert, values)

                    for column in columns:
                        if column.endswith("_id") or column in {
                            "price_brl",
                            "status",
                            "stock_quantity",
                        }:
                            index_name = f"idx_{table}_{column}"
                            connection.execute(
                                f'CREATE INDEX "{index_name}" '
                                f'ON "{table}" ("{column}")'
                            )

        os.replace(temporary_database, database_path)
    finally:
        if temporary_database.exists():
            temporary_database.unlink()

    return database_path


def create_sql_query_tool(database_path: Path) -> StructuredTool:
    def execute_read_only_query(query: str) -> str:
        statement = query.strip()
        if len(statement) > 10_000:
            return "Erro: a consulta excede o limite de tamanho permitido."
        if not re.match(r"(?is)^(SELECT|WITH)\b", statement):
            return "Erro: somente consultas SELECT são permitidas."
        if _has_unparenthesized_or(statement):
            return (
                "Erro: coloque condições alternativas OR entre parênteses quando "
                "combinadas com AND, para que os filtros de categoria, preço, "
                "status e estoque sejam aplicados corretamente."
            )

        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        try:
            with sqlite3.connect(uri, uri=True, timeout=5) as connection:
                def authorize(
                    action: int,
                    argument_one: str | None,
                    argument_two: str | None,
                    _database: str | None,
                    _source: str | None,
                ) -> int:
                    if action == sqlite3.SQLITE_READ:
                        table = (argument_one or "").lower()
                        column = (argument_two or "").lower()
                        if table == "customers":
                            return sqlite3.SQLITE_DENY
                        if table == "orders" and column in _PRIVATE_ORDER_COLUMNS:
                            return sqlite3.SQLITE_DENY
                        if table not in {
                            "categories",
                            "order_items",
                            "orders",
                            "products",
                            "promotions",
                        }:
                            return sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_FUNCTION:
                        function_name = (argument_two or "").lower()
                        if function_name in {"load_extension", "readfile", "writefile"}:
                            return sqlite3.SQLITE_DENY
                    if action in {
                        sqlite3.SQLITE_ATTACH,
                        sqlite3.SQLITE_DETACH,
                        sqlite3.SQLITE_PRAGMA,
                        sqlite3.SQLITE_INSERT,
                        sqlite3.SQLITE_UPDATE,
                        sqlite3.SQLITE_DELETE,
                        sqlite3.SQLITE_ALTER_TABLE,
                        sqlite3.SQLITE_CREATE_INDEX,
                        sqlite3.SQLITE_CREATE_TABLE,
                        sqlite3.SQLITE_CREATE_TRIGGER,
                        sqlite3.SQLITE_CREATE_VIEW,
                        sqlite3.SQLITE_DROP_INDEX,
                        sqlite3.SQLITE_DROP_TABLE,
                        sqlite3.SQLITE_DROP_TRIGGER,
                        sqlite3.SQLITE_DROP_VIEW,
                    }:
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK

                connection.set_authorizer(authorize)
                cursor = connection.execute(statement)
                columns = [description[0] for description in cursor.description or ()]
                rows = cursor.fetchmany(51)
        except sqlite3.Error as error:
            return f"Erro SQL: {error}"

        truncated = len(rows) > 50
        output = {
            "columns": columns,
            "rows": [dict(zip(columns, row)) for row in rows[:50]],
            "truncated": truncated,
        }
        return json.dumps(output, ensure_ascii=False, default=str)

    return StructuredTool.from_function(
        func=execute_read_only_query,
        name="consultar_banco_sql",
        description=(
            "Executa SQL somente de leitura sobre os CSVs importados. Use "
            "consultas SELECT. Tabelas e colunas: products(product_id, price_brl, "
            "name, category_id, description, stock_quantity, status, specs, "
            "created_at); categories(category_id, name, description); "
            "promotions(promotion_id, product_id, discount_percent, description, "
            "is_active); orders(order_id, customer_id, order_date, status, "
            "total_brl, payment_method, estimated_delivery); order_items(order_id, "
            "quantity, product_id). Relacione products.category_id com "
            "categories.category_id. Filtre pelo nome da categoria para restringir "
            "o tipo de instrumento; para violões, use categories.name LIKE "
            "'%viol%' para abranger o plural e evitar problemas de acentuação. "
            "Para itens disponíveis, use "
            "products.status = 'active' e products.stock_quantity > 0. Valores "
            "monetários em BRL são números. Condições alternativas com OR devem "
            "ficar entre parênteses quando combinadas com AND. Retorna até 50 linhas."
        ),
    )
