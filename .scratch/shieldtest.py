import asyncio

async def child():
    try:
        await asyncio.sleep(1)
    except asyncio.CancelledError:
        await asyncio.sleep(0)
        raise

async def waiter():
    print("waiter enter")
    t = asyncio.create_task(child())
    await asyncio.sleep(0.01)
    t.cancel()
    g = asyncio.gather(t, return_exceptions=True)
    try:
        print("before shield")
        await asyncio.shield(g)
        print("after shield")
    except asyncio.CancelledError:
        print("shield cancelled")
        await g
        print("gather settled")
        raise

async def worker():
    try:
        await asyncio.sleep(10)
    finally:
        print("finally enter")
        await asyncio.sleep(0)
        print("before waiter")
        await waiter()
        print("after waiter")

async def main():
    t = asyncio.create_task(worker())
    await asyncio.sleep(0.03)
    t.cancel()
    try:
        await t
    except BaseException as e:
        print("main got", type(e).__name__, e)

asyncio.run(main())
