from typing import Any, List, Optional, Sequence, Tuple, Type, Union
from functools import wraps
from .base import BaseSQLDBEngine, BaseSQLDBMapper
from .base import (TransactedConnections, 
        TransactedConnectionProxy,
        TransactedConnectionWrapper)
import asyncio
import asyncpg
from postmodel.exceptions import (OperationalError, 
        DBConnectionError, 
        IntegrityError, 
        TransactionManagementError)
from postmodel.main import Postmodel


def translate_exceptions(func):
    @wraps(func)
    async def translate_exceptions_(self, *args):
        try:
            return await func(self, *args)
        except asyncpg.SyntaxOrAccessError as exc:
            raise OperationalError(exc)
        except asyncpg.IntegrityConstraintViolationError as exc:
            raise IntegrityError(exc)
        except asyncpg.InvalidTransactionStateError as exc:  # pragma: nocoverage
            raise TransactionManagementError(exc)

    return translate_exceptions_


class PooledTransactionContext:

    __slots__ = ('name', 'token', 'timeout', 'connection', 'transaction', 'done', 'pool')

    def __init__(self, name, pool, timeout):
        self.name = name
        self.pool = pool
        self.timeout = timeout
        self.connection = None
        self.done = False
        self.transaction = None

    async def __aenter__(self):
        if self.connection is not None or self.done:
            raise Exception('a connection is already acquired')
        self.connection = await self.pool._acquire(self.timeout)
        self.transaction = self.connection.transaction()
        conn_proxy = TransactedConnectionProxy(self.connection)
        self.token = TransactedConnections.set(self.name, conn_proxy)
        await self.transaction.start()
        return conn_proxy

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            try:
                await self.transaction.rollback()
            except:
                pass
        else:
            await self.transaction.commit()
        self.done = True
        con = self.connection
        self.connection = None
        TransactedConnections.reset(self.name, self.token)
        await self.pool.release(con)


class PostgresMapper(BaseSQLDBMapper):
    async def create_table(self):
        raise NotImplementedError()

    async def insert(self, data):
        raise NotImplementedError()


class PostgresEngine(BaseSQLDBEngine):
    mapper_class = PostgresMapper
    default_config = {
        'min_size': 1,
        'max_size': 30,
    }
     
    def __init__(self, name,  config, parameters={}):
        super(PostgresEngine, self).__init__(name, config=config, parameters=parameters)
        self.user = self.config['username']
        self.password = self.config['password']
        self.database = self.config['db_path']
        self.host = self.config['hostname']
        self.port = int(self.config['port'])
        
        self._conn_params = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "database": self.database,
            **self.parameters
            }
        self._pool = None
        self._db_url = f'postgresql://{self.user}:{self.password}@{self.host}:{self.port}/'
    
    async def init(self):
        if not self._pool:
            await self._create_pool()

    async def _create_pool(self, create_db=True):
        if self._pool:	
            return	
        try:	
            self._pool = await asyncpg.create_pool(None, password=self.password, **self._conn_params)	
        except asyncpg.InvalidCatalogNameError:
            if create_db:
                await self.db_create()	
                self._pool = await asyncpg.create_pool(None, password=self.password, **self._conn_params)	
        except:	
            raise DBConnectionError(f"Can't establish connection to database {self.database}")
    
    async def close(self) -> None:
        await self._close()
    
    async def _close(self) -> None:
        if self._pool:  # pragma: nobranch
            try:
                await asyncio.wait_for(self._pool.close(), 10)
            except asyncio.TimeoutError:  # pragma: nocoverage
                self._pool.terminate()
            self._pool = None

    async def db_create(self) -> None:
        conn = await asyncpg.connect(self._db_url)
        try:
            await conn.execute(f'CREATE DATABASE "{self.database}" OWNER "{self.user}"')
        except Exception as e:
            raise OperationalError(f"create database {self.database}, error: {str(e)}")
        await conn.close()

    async def db_delete(self) -> None:
        await self.close()
        conn = await asyncpg.connect(self._db_url)
        try:
            await conn.execute(f'DROP DATABASE "{self.database}"')
        except Exception as e:  # pragma: nocoverage
            raise OperationalError(f"drop database {self.database}, error: {str(e)}")
        await conn.close()
    
    def in_transaction(self):
        transacted_conn =  self._current_transacted_conn()
        if transacted_conn:
            raise Exception('nested in_transaction not allowed.')
        else:
            return PooledTransactionContext(self.name, self._pool, timeout=None)
    
    def _current_transacted_conn(self):
        try:
            return TransactedConnections.get(self.name)
        except:
            return None 

    def acquire_connection(self, timeout=None):
        if not self._pool:
            self._create_pool()
        transacted_conn =  self._current_transacted_conn()
        if transacted_conn:
            return TransactedConnectionWrapper(transacted_conn)
        else:
            return self._pool.acquire(timeout=timeout)
    
    @translate_exceptions
    async def execute_insert(self, query: str, values: list) -> Optional[asyncpg.Record]:
        async with self.acquire_connection() as connection:
            return await connection.fetchrow(query, *values)
    
    @translate_exceptions
    async def execute_many(self, query: str, values: list) -> None:
        async with self.acquire_connection() as connection:
            async with connection.transaction():
                await connection.executemany(query, values)
    
    @translate_exceptions
    async def execute_query(
        self, query: str, values: Optional[list] = None
    ) -> Tuple[int, List[dict]]:
        async with self.acquire_connection() as connection:
            if values:
                params = [query, *values]
            else:
                params = [query]
            if query.startswith("UPDATE") or query.startswith("DELETE"):
                res = await connection.execute(*params)
                try:
                    rows_affected = int(res.split(" ")[1])
                except Exception:  # pragma: nocoverage
                    rows_affected = 0
                return rows_affected, []
            else:
                rows = await connection.fetch(*params)
                return len(rows), rows

    @translate_exceptions
    async def execute_query_dict(self, query: str, values: Optional[list] = None) -> List[dict]:
        async with self.acquire_connection() as connection:
            if values:
                return list(map(dict, await connection.fetch(query, *values)))
            return list(map(dict, await connection.fetch(query)))

    @translate_exceptions
    async def execute_script(self, query: str) -> None:
        async with self.acquire_connection() as connection:
            await connection.execute(query)