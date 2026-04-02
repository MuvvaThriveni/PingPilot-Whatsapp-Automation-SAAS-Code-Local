import asyncio
import os

from dotenv import load_dotenv
from database import init_db_pool, close_db_pool
from db_layer.quota import get_quota_status, try_consume_quota


if os.name == "nt":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main() -> None:
    load_dotenv()
    await init_db_pool()
    try:
        print(await get_quota_status("test_tenant_123", 5))

        print(await try_consume_quota("test_tenant_123", 2))  # True
        print(await try_consume_quota("test_tenant_123", 2))  # True
        print(await try_consume_quota("test_tenant_123", 2))  # Should be False
    finally:
        await close_db_pool()


asyncio.run(main())